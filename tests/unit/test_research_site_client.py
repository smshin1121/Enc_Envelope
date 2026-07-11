"""Unit tests for desktop.sync.research_site_client (no network)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from desktop.sync import research_site_client as client


# ---------------------------------------------------------------------------
# Canonical signature
# ---------------------------------------------------------------------------


def test_canonical_signature_matches_contract():
    secret = "test-secret"
    ts = "1767600000"
    nonce = "n-abc123"
    raw = b'{"seal_id":"S-20260711-ABCDEF"}'

    expected = hmac.new(
        secret.encode(),
        ts.encode() + b"\n" + nonce.encode() + b"\n" + raw,
        hashlib.sha256,
    ).hexdigest()

    assert client._canonical_signature(secret, ts, nonce, raw) == expected


# ---------------------------------------------------------------------------
# Payload preparation
# ---------------------------------------------------------------------------


def test_prepare_payload_maps_unlock_time_iso_into_process_info():
    record = json.dumps({
        "seal_id": "S-20260711-ABCDEF",
        "process_info": {"seal_type": "Sealing"},
        "unlock_time_iso": "2026-08-01T00:00:00Z",
    })
    payload = json.loads(client._prepare_payload(record))
    assert payload["process_info"]["unlock_time"] == "2026-08-01T00:00:00Z"


def test_prepare_payload_keeps_existing_unlock_time():
    record = json.dumps({
        "process_info": {"unlock_time": "2026-09-01T00:00:00Z"},
        "unlock_time_iso": "2026-08-01T00:00:00Z",
    })
    payload = json.loads(client._prepare_payload(record))
    assert payload["process_info"]["unlock_time"] == "2026-09-01T00:00:00Z"


def test_prepare_payload_attaches_pdf_base64(tmp_path):
    pdf = tmp_path / "record.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    payload = json.loads(client._prepare_payload("{}", str(pdf)))
    assert base64.b64decode(payload["record_pdf"]) == b"%PDF-1.4 dummy"


def test_prepare_payload_rejects_non_pdf(tmp_path):
    bogus = tmp_path / "record.pdf"
    bogus.write_bytes(b"not a pdf")
    with pytest.raises(client.ResearchSiteSyncError):
        client._prepare_payload("{}", str(bogus))


# ---------------------------------------------------------------------------
# push_seal_record / push_seal_record_safe
# ---------------------------------------------------------------------------


def test_push_raises_when_env_missing(monkeypatch):
    monkeypatch.delenv(client.ENV_BASE_URL, raising=False)
    monkeypatch.delenv(client.ENV_SECRET, raising=False)
    with pytest.raises(client.ResearchSiteSyncError):
        client.push_seal_record("{}")


def test_push_safe_skips_when_env_missing(monkeypatch):
    monkeypatch.delenv(client.ENV_BASE_URL, raising=False)
    monkeypatch.delenv(client.ENV_SECRET, raising=False)
    assert client.push_seal_record_safe("{}") is False


def test_push_sends_signed_request(monkeypatch):
    captured = {}

    class _FakeResponse:
        def read(self):
            return b'{"status": "success", "message": "ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _FakeResponse()

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    body = client.push_seal_record(
        '{"seal_id": "S-20260711-ABCDEF"}',
        base_url="https://example.org:1643/",
        secret="shared-secret",
    )
    assert body["status"] == "success"

    request = captured["request"]
    assert request.full_url == "https://example.org:1643/api/seal-records"
    ts = request.get_header("X-sync-timestamp")
    nonce = request.get_header("X-sync-nonce")
    sig = request.get_header("X-sync-signature")
    assert ts and nonce and sig
    assert sig == client._canonical_signature("shared-secret", ts, nonce, request.data)


def test_push_safe_swallows_http_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise client.urllib.error.URLError("connection refused")

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)
    assert (
        client.push_seal_record_safe(
            "{}", base_url="https://example.org", secret="s"
        )
        is False
    )
