from __future__ import annotations

import pytest

import main


def test_legacy_main_fails_before_loading_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_loaded = False

    def forbidden_load_config() -> object:
        nonlocal config_loaded
        config_loaded = True
        raise AssertionError("legacy main must not load config or dotenv")

    monkeypatch.setattr(main, "load_config", forbidden_load_config)

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 2
    assert config_loaded is False
    assert "Legacy main.py runtime is disabled" in capsys.readouterr().err


def test_legacy_guard_is_enabled() -> None:
    assert main.LEGACY_RUNTIME_DISABLED is True

