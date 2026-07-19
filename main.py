"""
main.py — Entry point for the Avellaneda-Stoikov Market Maker Bot.

Loads configuration, sets up the CCXT exchange for OKX, instantiates the
trading bot, and runs it via an async event loop.

Usage:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

from config import load_config, ConfigValidationError
from trading_bot import TradingBot

# ── Credential keys to redact from logs ─────────────────────────────────
_REDACT_KEYS = {"api_key", "api_secret", "api_passphrase", "secret", "password", "apiKey"}


class RedactingFilter(logging.Filter):
    """Filter that redacts known credential patterns from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for key in _REDACT_KEYS:
            # Simple pattern: key=value or key: value
            if key in msg.lower():
                record.msg = "[REDACTED — credential key detected]"
                record.args = ()
        return True


def _setup_logging(config) -> None:
    """Configure logging with rotation, credential redaction, and level control."""
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(RedactingFilter())
    root.addHandler(console)

    # Rotating file handler (if enabled)
    if config.log_to_file:
        log_dir = config.log_dir
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "bot.log")

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=config.log_rotation_mb * 1024 * 1024,
            backupCount=config.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactingFilter())
        root.addHandler(file_handler)

        # Separate error log
        error_path = os.path.join(log_dir, "bot_error.log")
        error_handler = RotatingFileHandler(
            error_path,
            maxBytes=config.log_rotation_mb * 1024 * 1024,
            backupCount=config.log_backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        error_handler.addFilter(RedactingFilter())
        root.addHandler(error_handler)

    # Reduce noise from external libs
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


def main() -> None:
    """Configure exchange, trading bot, and start the async event loop."""
    # ── Load config ─────────────────────────────────────────────────────
    try:
        config = load_config()
    except ConfigValidationError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Setup logging (after config is loaded) ──────────────────────────
    _setup_logging(config)

    logger.info("=" * 60)
    logger.info("  Avellaneda-Stoikov Market Maker — Starting")
    logger.info("=" * 60)
    logger.info(
        "Config loaded: symbol=%s, mode=%s, sandbox=%s, capital=%.1f, lot=%.4f",
        config.symbol, config.strategy_mode, config.sandbox,
        config.initial_capital, config.fixed_lot_size,
    )
    logger.info(
        "Dashboard will be available at http://%s:%d",
        config.dashboard_host, config.dashboard_port,
    )

    if not config.api_key or config.api_key == "your_api_key_here":
        logger.error(
            "API credentials not configured. "
            "Copy .env.example to .env and fill in your OKX API key, "
            "secret, and passphrase."
        )
        sys.exit(1)

    # ── Create and run the trading bot ──────────────────────────────────
    bot = TradingBot(config)

    # ── Register signal handlers ────────────────────────────────────────
    stop_event = bot.stop_event

    def _signal_handler(signum, frame):
        logger.info("Received signal %s — requesting shutdown", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (OSError, ValueError):
        pass  # SIGTERM not available on Windows in some contexts

    # ── Run ─────────────────────────────────────────────────────────────
    logger.info("Starting trading bot — Ctrl+C to stop")
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — shutdown complete")
    except Exception:
        logger.exception("Unhandled exception in trading bot")
        sys.exit(1)


if __name__ == "__main__":
    main()
