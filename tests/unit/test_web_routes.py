"""Flask route basic tests using test_client.

Covers:
  - GET / -> 200
  - POST /sync/upload-record -- valid JSON -> success
  - POST /sync/upload-record -- duplicate -> idempotent success
  - Unauthenticated suspect record access -> redirect/403
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest


@pytest.fixture()
def app(tmp_path: Any):
    """Create a fresh Flask app with a temporary SQLite DB."""
    db_path = str(tmp_path / "route_test.db")
    os.environ["USE_SQLITE"] = "true"
    os.environ["SQLITE_PATH"] = db_path

    # Patch the config class attribute before create_app reads it
    from web.config import TestingConfig
    original_path = TestingConfig.SQLITE_PATH
    TestingConfig.SQLITE_PATH = db_path

    from web.app import create_app

    app = create_app("testing")

    yield app

    TestingConfig.SQLITE_PATH = original_path


@pytest.fixture()
def client(app: Any):
    """Flask test client."""
    return app.test_client()


# ===================================================================
# Index route
# ===================================================================

class TestIndexRoute:
    """GET / -> 200."""

    def test_index_returns_200(self, client: Any) -> None:
        resp = client.get("/")
        assert resp.status_code == 200


# ===================================================================
# Sync upload-record
# ===================================================================

class TestSyncUploadRecord:
    """POST /sync/upload-record."""

    def _ensure_case(self, app: Any, seal_id: str) -> None:
        """Create a parent case so FK constraints pass."""
        with app.app_context():
            from web.models.db_models import find_case_by_seal_id, insert_case

            if not find_case_by_seal_id(seal_id):
                insert_case(
                    seal_id=seal_id,
                    case_number="C-ROUTE",
                    investigator="수사관",
                    suspect_name="홍길동",
                )

    def _valid_payload(self, seal_id: str = "S-ROUTE-001") -> dict[str, Any]:
        return {
            "seal_id": seal_id,
            "event_id": 1,
            "event_type": "Sealing",
            "record_json": json.dumps({"action": "seal"}),
        }

    def test_valid_json_success(self, app: Any, client: Any) -> None:
        self._ensure_case(app, "S-ROUTE-001")
        resp = client.post(
            "/sync/upload-record",
            json=self._valid_payload(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_duplicate_is_idempotent(self, app: Any, client: Any) -> None:
        self._ensure_case(app, "S-ROUTE-DUP")
        payload = self._valid_payload("S-ROUTE-DUP")

        resp1 = client.post("/sync/upload-record", json=payload)
        assert resp1.status_code == 200

        resp2 = client.post("/sync/upload-record", json=payload)
        assert resp2.status_code == 200
        assert resp2.get_json()["status"] == "ok"

    def test_missing_seal_id(self, client: Any) -> None:
        payload = {
            "event_id": 1,
            "event_type": "Sealing",
            "record_json": "{}",
        }
        resp = client.post("/sync/upload-record", json=payload)
        assert resp.status_code == 400

    def test_missing_event_id(self, client: Any) -> None:
        payload = {
            "seal_id": "S-001",
            "event_type": "Sealing",
            "record_json": "{}",
        }
        resp = client.post("/sync/upload-record", json=payload)
        assert resp.status_code == 400

    def test_invalid_event_type(self, client: Any) -> None:
        payload = {
            "seal_id": "S-001",
            "event_id": 1,
            "event_type": "BadType",
            "record_json": "{}",
        }
        resp = client.post("/sync/upload-record", json=payload)
        assert resp.status_code == 400

    def test_empty_record_json(self, client: Any) -> None:
        payload = {
            "seal_id": "S-001",
            "event_id": 1,
            "event_type": "Sealing",
            "record_json": "",
        }
        resp = client.post("/sync/upload-record", json=payload)
        assert resp.status_code == 400

    def test_non_json_request_rejected(self, client: Any) -> None:
        # Non-JSON POST triggers CSRF protection (403) before reaching route
        resp = client.post(
            "/sync/upload-record",
            data="plain text",
            content_type="text/plain",
        )
        assert resp.status_code in (400, 403)

    def test_with_base64_pdf(self, app: Any, client: Any) -> None:
        import base64

        self._ensure_case(app, "S-PDF-001")
        pdf_bytes = b"%PDF-1.4 fake"
        payload = {
            "seal_id": "S-PDF-001",
            "event_id": 1,
            "event_type": "Sealing",
            "record_json": json.dumps({"with_pdf": True}),
            "record_pdf": base64.b64encode(pdf_bytes).decode("ascii"),
        }
        resp = client.post("/sync/upload-record", json=payload)
        assert resp.status_code == 200

    def test_invalid_base64_pdf(self, client: Any) -> None:
        payload = {
            "seal_id": "S-PDF-BAD",
            "event_id": 1,
            "event_type": "Sealing",
            "record_json": "{}",
            "record_pdf": "!!!not-base64!!!",
        }
        resp = client.post("/sync/upload-record", json=payload)
        assert resp.status_code == 400


# ===================================================================
# Suspect records: auth required
# ===================================================================

class TestSuspectRecordsAuth:
    """Unauthenticated access to suspect records -> redirect."""

    def _register_case(self, app: Any, seal_id: str) -> None:
        with app.app_context():
            from web.models.db_models import insert_case

            insert_case(
                seal_id=seal_id,
                case_number="2025-AUTH-01",
                investigator="수사관A",
                suspect_name="홍길동",
                suspect_birth="19900101",
                suspect_phone="010-1234-5678",
                auth_level="basic",
            )

    def test_unauthenticated_records_redirects(
        self, app: Any, client: Any
    ) -> None:
        self._register_case(app, "S-AUTH-001")

        resp = client.get("/suspect/records/S-AUTH-001", follow_redirects=False)
        # Should redirect to auth page
        assert resp.status_code == 302
        assert "/suspect/auth/S-AUTH-001" in resp.headers.get("Location", "")

    def test_authenticated_records_accessible(
        self, app: Any, client: Any
    ) -> None:
        self._register_case(app, "S-AUTH-002")

        # Simulate authentication by setting session
        with client.session_transaction() as sess:
            sess["auth_S-AUTH-002"] = True

        resp = client.get("/suspect/records/S-AUTH-002")
        assert resp.status_code == 200

    def test_unauthenticated_upload_share_redirects(
        self, app: Any, client: Any
    ) -> None:
        self._register_case(app, "S-AUTH-003")

        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"

        resp = client.post(
            "/suspect/upload-share/S-AUTH-003",
            data={
                "seal_id": "S-AUTH-003",
                "share_data": "some-share",
                "csrf_token": "test-token",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/suspect/auth" in resp.headers.get("Location", "")


# ===================================================================
# Investigator routes: basic smoke tests
# ===================================================================

class TestInvestigatorRoutes:
    """Basic investigator route smoke tests."""

    def test_register_case_get(self, client: Any) -> None:
        resp = client.get("/investigator/register-case")
        assert resp.status_code == 200

    def test_upload_share_get(self, client: Any) -> None:
        resp = client.get("/investigator/upload-share")
        assert resp.status_code == 200

    def test_recover_key_get(self, client: Any) -> None:
        resp = client.get("/investigator/recover-key")
        assert resp.status_code == 200

    def test_register_case_post_missing_fields(self, client: Any) -> None:
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"

        resp = client.post(
            "/investigator/register-case",
            data={"csrf_token": "test-token"},
        )
        assert resp.status_code == 400


# ===================================================================
# Error handlers
# ===================================================================

class TestErrorHandlers:
    """Custom error pages return correct status codes."""

    def test_404_page(self, client: Any) -> None:
        resp = client.get("/nonexistent-page-xyz")
        assert resp.status_code == 404

    def test_suspect_auth_nonexistent_case(self, client: Any) -> None:
        resp = client.get("/suspect/auth/NONEXISTENT")
        assert resp.status_code == 404
