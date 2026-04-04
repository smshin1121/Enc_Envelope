"""Integration test: TSA fallback behavior.

Validates:
- check_unlock_time() falls back to local time when TSA URL is invalid
- Warning message is included in the result
- Local fallback correctly determines allowed/denied based on time
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from desktop.crypto import check_unlock_time
from desktop.crypto.types import AccessCheckResult


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTSAFallback:
    """TSA fallback behavior when TSA server is unreachable."""

    def test_invalid_tsa_url_falls_back_to_local_time(self) -> None:
        """When TSA URL is wrong, check_unlock_time should use local
        fallback and return allowed=True with a warning."""

        # Set unlock_time to 1 day in the past so local time passes
        past_time = datetime.now(tz=timezone.utc) - timedelta(days=1)
        unlock_iso = past_time.isoformat()

        result = check_unlock_time(
            unlock_time_iso=unlock_iso,
            tsa_url="https://invalid-tsa-server.example.com/tsa",
        )

        assert isinstance(result, AccessCheckResult)
        assert result.allowed is True
        assert result.method == "local_fallback"
        assert result.warning is not None
        assert "TSA" in result.warning

    def test_invalid_tsa_url_denied_when_future_unlock(self) -> None:
        """When unlock_time is in the future, access should be denied
        even before TSA is attempted (local preliminary check)."""

        future_time = datetime.now(tz=timezone.utc) + timedelta(days=30)
        unlock_iso = future_time.isoformat()

        result = check_unlock_time(
            unlock_time_iso=unlock_iso,
            tsa_url="https://invalid-tsa-server.example.com/tsa",
        )

        assert isinstance(result, AccessCheckResult)
        assert result.allowed is False
        assert result.method == "local_preliminary"
        # No warning because local preliminary check denied before TSA
        assert result.warning is None

    def test_no_tsa_url_falls_back_with_warning(self) -> None:
        """When no TSA URL is provided, local fallback with warning."""

        past_time = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        unlock_iso = past_time.isoformat()

        result = check_unlock_time(
            unlock_time_iso=unlock_iso,
            tsa_url=None,
        )

        assert isinstance(result, AccessCheckResult)
        assert result.allowed is True
        assert result.method == "local_fallback"
        assert result.warning is not None
        assert "TSA" in result.warning or "local" in result.warning.lower()

    def test_fallback_warning_contains_descriptive_message(self) -> None:
        """Warning message should clearly indicate TSA was unavailable."""

        past_time = datetime.now(tz=timezone.utc) - timedelta(days=1)
        unlock_iso = past_time.isoformat()

        # Test with invalid URL
        result_invalid = check_unlock_time(
            unlock_time_iso=unlock_iso,
            tsa_url="https://invalid-tsa-server.example.com/tsa",
        )
        assert "unavailable" in result_invalid.warning.lower() or \
               "local" in result_invalid.warning.lower()

        # Test with no URL
        result_no_url = check_unlock_time(
            unlock_time_iso=unlock_iso,
            tsa_url=None,
        )
        assert "local time" in result_no_url.warning.lower() or \
               "TSA" in result_no_url.warning

    def test_unlock_time_iso_format_variations(self) -> None:
        """check_unlock_time should handle various ISO 8601 formats."""

        past = datetime.now(tz=timezone.utc) - timedelta(hours=2)

        # Standard ISO with timezone
        result1 = check_unlock_time(
            unlock_time_iso=past.isoformat(),
            tsa_url=None,
        )
        assert result1.allowed is True

        # Without microseconds
        past_no_micro = past.replace(microsecond=0)
        result2 = check_unlock_time(
            unlock_time_iso=past_no_micro.isoformat(),
            tsa_url=None,
        )
        assert result2.allowed is True

    def test_exactly_at_unlock_time_boundary(self) -> None:
        """Access should be allowed when current time equals unlock time
        (edge case: unlock_time is now or slightly in the past)."""

        # Use a time safely in the past to avoid race conditions
        just_past = datetime.now(tz=timezone.utc) - timedelta(seconds=5)
        unlock_iso = just_past.isoformat()

        result = check_unlock_time(
            unlock_time_iso=unlock_iso,
            tsa_url=None,
        )

        assert result.allowed is True
        assert result.method == "local_fallback"
