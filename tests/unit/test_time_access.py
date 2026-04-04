"""Tests for time-based access control (unlock_time verification).

Validates:
- Past unlock_time (before now) -> access allowed
- Future unlock_time (after now) -> access denied
- No TSA URL -> local_fallback with warning
- Invalid ISO 8601 -> AccessControlError
- Result fields populated correctly
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from desktop.crypto import AccessControlError, check_unlock_time
from desktop.crypto.types import AccessCheckResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _past_iso(hours: int = 1) -> str:
    """Return an ISO 8601 timestamp *hours* in the past."""
    dt = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _future_iso(hours: int = 1) -> str:
    """Return an ISO 8601 timestamp *hours* in the future."""
    dt = datetime.now(tz=timezone.utc) + timedelta(hours=hours)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Past unlock_time -> allowed
# ---------------------------------------------------------------------------

class TestPastUnlockTime:
    """When unlock_time is in the past, access must be allowed."""

    def test_access_allowed_no_tsa(self) -> None:
        result = check_unlock_time(_past_iso(1))

        assert isinstance(result, AccessCheckResult)
        assert result.allowed is True
        assert result.method == "local_fallback"

    def test_access_allowed_far_past(self) -> None:
        result = check_unlock_time("2020-01-01T00:00:00+00:00")

        assert result.allowed is True

    def test_warning_present_without_tsa(self) -> None:
        result = check_unlock_time(_past_iso(1))

        assert result.warning is not None
        assert "TSA" in result.warning or "local" in result.warning.lower()


# ---------------------------------------------------------------------------
# Future unlock_time -> denied
# ---------------------------------------------------------------------------

class TestFutureUnlockTime:
    """When unlock_time is in the future, access must be denied."""

    def test_access_denied_no_tsa(self) -> None:
        result = check_unlock_time(_future_iso(1))

        assert result.allowed is False
        assert result.method == "local_preliminary"

    def test_access_denied_far_future(self) -> None:
        result = check_unlock_time("2099-12-31T23:59:59+00:00")

        assert result.allowed is False

    def test_no_warning_on_denial(self) -> None:
        result = check_unlock_time(_future_iso(1))

        assert result.warning is None


# ---------------------------------------------------------------------------
# TSA URL not provided -> local fallback
# ---------------------------------------------------------------------------

class TestNoTsaUrl:
    """Without a TSA URL, the system must fall back to local time."""

    def test_local_fallback_method(self) -> None:
        result = check_unlock_time(_past_iso(1), tsa_url=None)

        assert result.method == "local_fallback"

    def test_warning_mentions_no_tsa(self) -> None:
        result = check_unlock_time(_past_iso(1), tsa_url=None)

        assert result.warning is not None
        assert "No TSA URL" in result.warning or "local" in result.warning.lower()


# ---------------------------------------------------------------------------
# TSA URL provided but unreachable -> local fallback
# ---------------------------------------------------------------------------

class TestTsaUnavailable:
    """When TSA is unreachable, the system must fall back to local time."""

    def test_fallback_on_tsa_failure(self) -> None:
        result = check_unlock_time(
            _past_iso(1),
            tsa_url="http://192.0.2.1:9999/tsa",  # non-routable
        )

        assert result.allowed is True
        assert result.method == "local_fallback"
        assert result.warning is not None


# ---------------------------------------------------------------------------
# Result fields validation
# ---------------------------------------------------------------------------

class TestResultFields:
    """AccessCheckResult must have all required fields populated."""

    def test_current_time_is_iso8601(self) -> None:
        result = check_unlock_time(_past_iso(1))

        # Should parse without error
        dt = datetime.fromisoformat(result.current_time_iso)
        assert dt.tzinfo is not None

    def test_unlock_time_preserved(self) -> None:
        unlock = _past_iso(2)
        result = check_unlock_time(unlock)

        assert result.unlock_time_iso == unlock

    def test_frozen_dataclass(self) -> None:
        result = check_unlock_time(_past_iso(1))

        with pytest.raises(AttributeError):
            result.allowed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------

class TestInvalidInput:
    """Invalid ISO 8601 strings must raise AccessControlError."""

    def test_garbage_string_raises(self) -> None:
        with pytest.raises(AccessControlError):
            check_unlock_time("not-a-date")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(AccessControlError):
            check_unlock_time("")

    def test_none_raises(self) -> None:
        with pytest.raises((AccessControlError, TypeError)):
            check_unlock_time(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TSA success mock
# ---------------------------------------------------------------------------

class TestTsaSuccess:
    """When TSA responds successfully, method should be 'tsa'."""

    def test_tsa_method_on_success(self) -> None:
        past_time = _past_iso(1)
        fake_tsa_time = datetime.now(tz=timezone.utc)

        mock_result = AccessCheckResult(
            allowed=True,
            method="tsa",
            current_time_iso=fake_tsa_time.isoformat(),
            unlock_time_iso=past_time,
            warning=None,
        )

        with patch(
            "desktop.crypto.time_access_control._verify_with_tsa",
            return_value=mock_result,
        ):
            result = check_unlock_time(
                past_time,
                tsa_url="http://example.com/tsa",
            )

        assert result.allowed is True
        assert result.method == "tsa"
        assert result.warning is None
