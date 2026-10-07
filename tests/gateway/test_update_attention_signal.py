"""Boot-path update-convergence signal (``update_needs_attention``) contract tests.

The gateway NEVER resumes an update itself (a runtime process pulling code is the
mixed-sys.modules hazard the update pipeline exists to prevent). What it does on
boot — ``_flag_incomplete_update_on_boot`` — is a read-only duty check over the
latest update receipt: light the signal when the last update failed/refused or its
post-update sha no longer matches the running checkout, clear it explicitly when
the receipt is clean (the "starting" write reloads the previous gateway_state.json,
so a stale True survives unless overwritten).

No pytest tmp_path fixture: this file must also run outside the repo's hermetic
harness (the guard-level scenarios run in plain tempdirs), and every scenario
monkeypatches HERMES_HOME before importing gateway modules.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def boot_env(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an update_receipts dir; returns helpers."""
    home = tmp_path / "hermes_test"
    (home / "logs" / "update_receipts").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("gateway.status.publish_runtime_status", lambda **f: _PUBLISHED.update(f),
                        raising=False)
    _PUBLISHED.clear()
    return home


_PUBLISHED: dict = {}


def _write_receipt(home: Path, payload: dict) -> None:
    (home / "logs" / "update_receipts" / "latest.json").write_text(json.dumps(payload))


def _run_check(monkeypatch, running_sha):
    import gateway.run_adapters as ra
    import hermes_cli.version_info as vi
    monkeypatch.setattr(vi, "get_code_identity", lambda refresh=False: {"sha": running_sha})
    class _Self:  # the method lives on the adapter-lifecycle mixin; no runner needed
        pass
    ra.GatewayAdapterLifecycleMixin._flag_incomplete_update_on_boot(_Self())


def test_failed_receipt_lights_signal(boot_env, monkeypatch):
    _write_receipt(boot_env, {"outcome": "failed", "post_update": {"sha": "a" * 40}})
    _run_check(monkeypatch, "a" * 40)
    assert _PUBLISHED.get("update_needs_attention") is True
    assert _PUBLISHED["update_attention_reasons"] == ["last update outcome: failed"]


def test_clean_receipt_clears_signal(boot_env, monkeypatch):
    _write_receipt(boot_env, {"outcome": "success", "post_update": {"sha": "a" * 40}})
    _run_check(monkeypatch, "a" * 40)
    assert _PUBLISHED == {"update_needs_attention": False, "update_attention_reasons": None}


def test_sha_mismatch_lights_signal(boot_env, monkeypatch):
    _write_receipt(boot_env, {"outcome": "success", "post_update": {"sha": "a" * 40}})
    _run_check(monkeypatch, "b" * 40)
    assert _PUBLISHED.get("update_needs_attention") is True
    assert any("differs" in r for r in _PUBLISHED["update_attention_reasons"])


def test_empty_receipt_is_noop(boot_env, monkeypatch):
    _write_receipt(boot_env, {})
    _run_check(monkeypatch, "a" * 40)
    assert _PUBLISHED == {}


def test_status_fields_roundtrip(tmp_path, monkeypatch):
    """The two new fields survive the payload merge the receipt/handoff seam serializes."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.status import _prepare_runtime_status_update

    _, payload, _ = _prepare_runtime_status_update(
        reload_existing=True,
        update_needs_attention=True,
        update_attention_reasons=["last update outcome: failed"],
    )
    assert payload["update_needs_attention"] is True
    assert payload["update_attention_reasons"] == ["last update outcome: failed"]

    _, cleared, _ = _prepare_runtime_status_update(
        update_needs_attention=False, update_attention_reasons=None)
    assert cleared["update_needs_attention"] is False
    assert cleared["update_attention_reasons"] is None
