from __future__ import annotations

import json
import logging

import pytest

from myboxi_agent.cli import main
from myboxi_agent.config import Settings
from myboxi_agent.logconfig import REDACTED, JsonFormatter, redact


def test_defaults_follow_spec_paths() -> None:
    s = Settings()
    assert str(s.db_path) == "/var/lib/myboxi/myboxi.db"  # SPEC §4
    assert str(s.asset_dir) == "/var/lib/myboxi/assets"
    assert s.default_server_url is None  # set by the image, never by accident in dev
    assert s.pins == {"play_pause": 17, "volume_up": 27, "volume_down": 22, "next": 23}


@pytest.mark.parametrize(
    "raw",
    [
        '{"device_secret": "abc123SECRET"}',
        "GET /api/v1/pairing/poll?poll_token=abc123SECRET",
        "soloist_key=abc123SECRET",
        "Authorization: Bearer abc123SECRET.x.y",
        "mqtt_password=abc123SECRET",
        '{"pairing_key": "abc123SECRET"}',
    ],
)
def test_secrets_are_redacted(raw: str) -> None:
    out = redact(raw)
    assert "abc123SECRET" not in out
    assert REDACTED in out


def test_json_formatter_redacts_extras() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "x", (), None)
    record.device_secret = "abc123SECRET"
    record.mqtt_password = "abc123SECRET"
    record.token_id = "not-a-secret"
    out = json.loads(JsonFormatter().format(record))
    assert out["device_secret"] == out["mqtt_password"] == REDACTED
    assert out["token_id"] == "not-a-secret"


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert "myboxi-agent" in capsys.readouterr().out


def test_code_explains_the_pairing_state() -> None:
    """``myboxi code`` over SSH, for a box without a speaker (SPEC §9.5)."""
    from myboxi_agent.cli import describe_pairing

    assert "471 193" in describe_pairing({"paired": False, "pairing_code": "471193"})
    assert "is paired" in describe_pairing({"paired": True, "pairing_code": None})
    waiting = describe_pairing({"paired": False, "pairing_code": None,
                                "server_url": "https://app.myboxi.eu",
                                "last_error": "unreachable: timeout"})  # fmt: skip
    assert "https://app.myboxi.eu" in waiting
    assert "unreachable: timeout" in waiting
