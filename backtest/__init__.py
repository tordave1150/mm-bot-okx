"""
backtest/ — Offline backtest + stress-test framework for mm-bot-okx.

Reuses production modules (market_state, regime_detector, quote_engine,
risk_manager, fill_tracker) unchanged, feeding them synthetic price ticks
instead of live exchange data.

No production files are modified during any backtest run.
"""
