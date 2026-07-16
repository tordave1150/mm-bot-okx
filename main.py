"""
main.py — Entry point for the Avellaneda-Stoikov Market Maker Bot.

Loads configuration, sets up the CCXT broker for OKX, instantiates the
strategy, and runs it via Lumibot's Trader.

Usage:
    python main.py
"""

from __future__ import annotations

import logging
import signal
import sys

from lumibot.brokers import Ccxt
from lumibot.traders import Trader

from config import load_config
from strategy import AvellanedaMarketMaker

# ── Logging setup ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Reduce noise from external libs
logging.getLogger("ccxt").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
# Suppress Lumibot's per-iteration spam ("Bot is running" / "Trading iteration ended")
logging.getLogger("lumibot.strategies._strategy").setLevel(logging.WARNING)
logging.getLogger("lumibot.strategies.strategy_executor").setLevel(logging.WARNING)
logging.getLogger("lumibot.brokers.broker").setLevel(logging.WARNING)


def main() -> None:
    """Configure broker, strategy, and start the trader."""
    logger.info("=" * 60)
    logger.info("  Avellaneda-Stoikov Market Maker — Starting")
    logger.info("=" * 60)

    # ── Load config ─────────────────────────────────────────────────────
    config = load_config()
    logger.info("Config loaded: symbol=%s, mode=%s, sandbox=%s",
                config.symbol, config.strategy_mode, config.sandbox)
    logger.info("Dashboard will be available at http://%s:%d",
                config.dashboard_host, config.dashboard_port)

    if not config.api_key or config.api_key == "your_api_key_here":
        logger.error(
            "API credentials not configured. "
            "Copy .env.example to .env and fill in your OKX API key, "
            "secret, and passphrase."
        )
        sys.exit(1)

    # ── Set up CCXT broker ──────────────────────────────────────────────
    broker_config = {
        "exchange_id": config.exchange_name,
        "apiKey": config.api_key,
        "secret": config.api_secret,
        "password": config.api_passphrase,  # OKX passphrase
        "sandbox": config.sandbox,
    }

    try:
        broker = Ccxt(broker_config)
        # Monkey-patch Lumibot's CCXT broker to bypass unimplemented OKX methods.
        # We track positions and balances natively within our strategy via fill_tracker.
        broker._pull_positions = lambda strategy: []
        broker._get_balances_at_broker = lambda quote_asset, strategy: (0.0, 0.0, 0.0)
    except Exception:
        logger.exception("Failed to create CCXT broker")
        sys.exit(1)

    # ── Create strategy ─────────────────────────────────────────────────
    strategy = AvellanedaMarketMaker(
        broker=broker,
        parameters={"config": config},
    )

    # ── Graceful shutdown handler ───────────────────────────────────────
    trader = Trader()
    trader.add_strategy(strategy)

    def _shutdown(signum, frame):
        logger.info("Received signal %s — shutting down", signum)
        try:
            strategy.on_abrupt_closing()
        except Exception:
            pass
        # Use os._exit to bypass Rich Live display and force-quit immediately.
        # This is needed on Windows where sys.exit() can be caught by threads.
        import os
        os._exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (OSError, ValueError):
        pass  # SIGTERM not available on Windows in some contexts

    # ── Run ─────────────────────────────────────────────────────────────
    logger.info("Starting trader — Ctrl+C to stop")
    try:
        trader.run_all()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — shutting down")
        try:
            strategy.on_abrupt_closing()
        except Exception:
            pass
        import os
        os._exit(0)
    except Exception:
        logger.exception("Unhandled exception in trader")
        try:
            strategy.on_abrupt_closing()
        except Exception:
            pass
        import os
        os._exit(1)


if __name__ == "__main__":
    main()
