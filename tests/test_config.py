"""
tests/test_config.py — Unit tests for configuration invariants.

Validates AGENTS.md §7 requirements:
  - 300 USDT baseline
  - fixed_lot_size == 0.01
  - Sandbox enforcement
  - Max inventory is lot-based
"""

from __future__ import annotations

import pytest
from config import Config, ConfigValidationError, load_config, _validate_config


class TestConfigDefaults:
    """Verify default configuration matches 300 USDT baseline."""

    def test_initial_capital_is_300(self):
        cfg = Config()
        assert cfg.initial_capital == 300.0

    def test_fixed_lot_size_is_001(self):
        cfg = Config()
        assert cfg.fixed_lot_size == 0.01

    def test_max_inventory_lots_is_int(self):
        cfg = Config()
        assert isinstance(cfg.max_inventory_lots, int)
        assert cfg.max_inventory_lots >= 1

    def test_max_inventory_is_lot_based(self):
        cfg = Config()
        assert cfg.max_inventory == cfg.fixed_lot_size * cfg.max_inventory_lots

    def test_sandbox_is_true(self):
        cfg = Config()
        assert cfg.sandbox is True

    def test_leverage_at_least_1(self):
        cfg = Config()
        assert cfg.leverage >= 1.0

    def test_fee_rates_non_negative(self):
        cfg = Config()
        assert cfg.maker_fee_rate >= 0
        assert cfg.taker_fee_rate >= 0


class TestConfigValidation:
    """Test that _validate_config catches violations."""

    def test_rejects_wrong_lot_size(self):
        cfg = Config(fixed_lot_size=0.02)
        with pytest.raises(ConfigValidationError, match="fixed_lot_size"):
            _validate_config(cfg)

    def test_rejects_zero_capital(self):
        cfg = Config(initial_capital=0.0)
        with pytest.raises(ConfigValidationError, match="initial_capital"):
            _validate_config(cfg)

    def test_rejects_negative_capital(self):
        cfg = Config(initial_capital=-100.0)
        with pytest.raises(ConfigValidationError, match="initial_capital"):
            _validate_config(cfg)

    def test_rejects_sandbox_false(self):
        cfg = Config(sandbox=False)
        with pytest.raises(ConfigValidationError, match="sandbox"):
            _validate_config(cfg)

    def test_rejects_zero_inventory_lots(self):
        cfg = Config(max_inventory_lots=0)
        with pytest.raises(ConfigValidationError, match="max_inventory_lots"):
            _validate_config(cfg)

    def test_rejects_excessive_inventory_lots(self):
        cfg = Config(max_inventory_lots=11)
        with pytest.raises(ConfigValidationError, match="max_inventory_lots"):
            _validate_config(cfg)

    def test_rejects_leverage_below_1(self):
        cfg = Config(leverage=0.5)
        with pytest.raises(ConfigValidationError, match="leverage"):
            _validate_config(cfg)

    def test_rejects_bad_drawdown_pct(self):
        cfg = Config(max_drawdown_pct=0.0)
        with pytest.raises(ConfigValidationError, match="max_drawdown_pct"):
            _validate_config(cfg)

    def test_accepts_valid_config(self):
        cfg = Config()
        _validate_config(cfg)  # Should not raise


class TestMaxInventoryProperty:
    """Verify max_inventory computed property."""

    def test_default_max_inventory(self):
        cfg = Config()
        assert cfg.max_inventory == 0.01  # 0.01 * 1

    def test_max_inventory_with_2_lots(self):
        cfg = Config(max_inventory_lots=2)
        assert cfg.max_inventory == pytest.approx(0.02)

    def test_max_inventory_with_3_lots(self):
        cfg = Config(max_inventory_lots=3)
        assert cfg.max_inventory == pytest.approx(0.03)
