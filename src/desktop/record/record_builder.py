"""JSON record construction for seal/unseal/reseal procedures.

Builds immutable record dictionaries conforming to the seal record schema.
All timestamps use ISO 8601 UTC format.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from datetime import datetime, timezone

from .exceptions import RecordValidationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEAL_ID_PATTERN = re.compile(r"^S-\d{8}-[0-9A-F]{6}$")
_ISO8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
)
_VALID_PROCESS_TYPES = frozenset({"Sealing", "Unsealing", "Resealing"})
_TOP_LEVEL_FIELDS = frozenset({
    "seal_id", "case_info", "process_info",
    "file_info", "signer_info", "history",
})

# ---------------------------------------------------------------------------
# seal_id generation
# ---------------------------------------------------------------------------


def create_seal_id() -> str:
    """Generate a unique seal ID in S-YYYYMMDD-XXXXXX format.

    The date portion uses UTC. The 6-character hex suffix is generated
    from OS-level secure random bytes (uppercase).

    Returns:
        A string like ``S-20260401-A3F1B2``.
    """
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    random_hex = os.urandom(3).hex().upper()
    return f"S-{date_part}-{random_hex}"


# ---------------------------------------------------------------------------
# Record builders (immutable — always return new dicts)
# ---------------------------------------------------------------------------


def build_seal_record(
    seal_id: str,
    case_info: dict,
    process_info: dict,
    file_info: dict,
    signer_info: dict,
    history: dict,
) -> dict:
    """Build a sealing record (봉인지) JSON structure.

    All input dicts are deep-copied so the caller's data is never mutated.

    Args:
        seal_id: Unique seal identifier (``S-YYYYMMDD-XXXXXX``).
        case_info: Case metadata (case_number, investigator, etc.).
        process_info: Process metadata (type must be ``"Sealing"``).
        file_info: File lists and hash match information.
        signer_info: Subject (피압수자) identity and certificate info.
        history: History object with summary and events.

    Returns:
        A new dict representing the complete seal record.
    """
    record: dict = {
        "seal_id": seal_id,
        "case_info": copy.deepcopy(case_info),
        "process_info": copy.deepcopy(process_info),
        "file_info": _ensure_file_info_defaults(copy.deepcopy(file_info)),
        "signer_info": copy.deepcopy(signer_info),
        "history": copy.deepcopy(history),
    }
    return record


def build_unseal_record(
    prev_record: dict,
    process_info: dict,
    file_info: dict,
) -> dict:
    """Build an unsealing record (봉인해제기록지).

    Carries forward ``seal_id``, ``case_info``, and ``signer_info``
    from the previous record.  ``history`` is inherited as-is; the
    caller is responsible for appending the unseal event beforehand.

    Args:
        prev_record: The most recent seal/reseal record.
        process_info: Process metadata (type must be ``"Unsealing"``).
        file_info: Decrypted file information and hash verification.

    Returns:
        A new dict representing the unseal record.
    """
    prev = copy.deepcopy(prev_record)
    record: dict = {
        "seal_id": prev["seal_id"],
        "case_info": prev["case_info"],
        "process_info": copy.deepcopy(process_info),
        "file_info": _ensure_file_info_defaults(copy.deepcopy(file_info)),
        "signer_info": prev["signer_info"],
        "history": prev["history"],
    }
    return record


def build_reseal_record(
    prev_record: dict,
    process_info: dict,
    file_info: dict,
    unknown_files: list | None = None,
    derived_files: list | None = None,
) -> dict:
    """Build a resealing record (재봉인기록지).

    Carries forward ``seal_id``, ``case_info``, and ``signer_info``
    from the previous record.  Unknown and derived file lists are
    merged into ``file_info``.

    Args:
        prev_record: The most recent unseal record.
        process_info: Process metadata (type must be ``"Resealing"``).
        file_info: Re-encrypted file information.
        unknown_files: Files not present in the previous record.
        derived_files: Files derived from originals during analysis.

    Returns:
        A new dict representing the reseal record.
    """
    prev = copy.deepcopy(prev_record)
    fi = copy.deepcopy(file_info)
    fi["unknown_files"] = copy.deepcopy(unknown_files or [])
    fi["derived_files"] = copy.deepcopy(derived_files or [])
    fi = _ensure_file_info_defaults(fi)

    record: dict = {
        "seal_id": prev["seal_id"],
        "case_info": prev["case_info"],
        "process_info": copy.deepcopy(process_info),
        "file_info": fi,
        "signer_info": prev["signer_info"],
        "history": prev["history"],
    }
    return record


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def validate_record(record: dict) -> list[str]:
    """Validate a record against the seal record schema.

    Returns a list of human-readable error strings.  An empty list
    means the record is valid.
    """
    errors: list[str] = []

    # 1. Top-level fields
    missing = _TOP_LEVEL_FIELDS - set(record.keys())
    if missing:
        errors.append(f"Missing top-level fields: {sorted(missing)}")

    # 2. seal_id format
    seal_id = record.get("seal_id", "")
    if not _SEAL_ID_PATTERN.match(seal_id):
        errors.append(
            f"seal_id '{seal_id}' does not match S-YYYYMMDD-XXXXXX format"
        )

    # 3. process_info.type
    proc = record.get("process_info", {})
    proc_type = proc.get("type", "")
    if proc_type not in _VALID_PROCESS_TYPES:
        errors.append(
            f"process_info.type '{proc_type}' is not one of "
            f"{sorted(_VALID_PROCESS_TYPES)}"
        )

    # 4. ISO 8601 time fields
    _validate_time_fields(record, errors)

    # 5. file_info.original_files non-empty
    fi = record.get("file_info", {})
    orig = fi.get("original_files", [])
    if not orig:
        errors.append("file_info.original_files must not be empty")

    # 6. result_files array length consistency
    _validate_result_files_consistency(fi, errors)

    # 7. history consistency
    _validate_history_consistency(record, errors)

    # 8. case_info required fields
    _validate_case_info(record.get("case_info", {}), errors)

    # 9. signer_info required fields
    _validate_signer_info(record.get("signer_info", {}), errors)

    return errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_file_info_defaults(fi: dict) -> dict:
    """Return a new file_info dict with default lists for optional fields."""
    return {
        **fi,
        "unknown_files": fi.get("unknown_files", []),
        "derived_files": fi.get("derived_files", []),
    }


def _validate_time_fields(record: dict, errors: list[str]) -> None:
    """Check that all time-related fields are valid ISO 8601 UTC."""
    time_paths = [
        ("process_info", "start_time"),
        ("process_info", "end_time"),
        ("case_info", "seizure_time"),
    ]
    for section, field in time_paths:
        value = record.get(section, {}).get(field, "")
        if value and not _ISO8601_PATTERN.match(value):
            errors.append(
                f"{section}.{field} '{value}' is not valid ISO 8601 UTC"
            )

    # Validate times inside original_files
    for idx, f in enumerate(
        record.get("file_info", {}).get("original_files", [])
    ):
        for tf in ("mtime", "ctime", "atime"):
            val = f.get(tf, "")
            if val and not _ISO8601_PATTERN.match(val):
                errors.append(
                    f"file_info.original_files[{idx}].{tf} "
                    f"'{val}' is not valid ISO 8601 UTC"
                )

    # Validate times inside history events
    for idx, ev in enumerate(
        record.get("history", {}).get("events", [])
    ):
        for tf in ("start_time", "end_time"):
            val = ev.get(tf, "")
            if val and not _ISO8601_PATTERN.match(val):
                errors.append(
                    f"history.events[{idx}].{tf} "
                    f"'{val}' is not valid ISO 8601 UTC"
                )


def _validate_result_files_consistency(
    fi: dict, errors: list[str]
) -> None:
    """Check that nonces/tags/chunk_lengths have matching lengths."""
    for idx, rf in enumerate(fi.get("result_files", [])):
        nonces = rf.get("nonces", [])
        tags = rf.get("tags", [])
        chunks = rf.get("chunk_lengths", [])
        lengths = {len(nonces), len(tags), len(chunks)}
        if len(lengths) > 1:
            errors.append(
                f"file_info.result_files[{idx}]: nonces({len(nonces)}), "
                f"tags({len(tags)}), chunk_lengths({len(chunks)}) "
                f"must have equal length"
            )


def _validate_history_consistency(record: dict, errors: list[str]) -> None:
    """Check that history.summary matches events count."""
    history = record.get("history", {})
    summary = history.get("summary", "")
    events = history.get("events", [])

    if not summary:
        errors.append("history.summary is empty or missing")
        return

    # Parse S{n}U{m}R{k} from summary
    match = re.match(r"^S(\d+)U(\d+)R(\d+)$", summary)
    if not match:
        errors.append(
            f"history.summary '{summary}' does not match S{{n}}U{{m}}R{{k}} "
            f"format"
        )
        return

    expected_total = int(match.group(1)) + int(match.group(2)) + int(
        match.group(3)
    )
    if expected_total != len(events):
        errors.append(
            f"history.summary '{summary}' implies {expected_total} events "
            f"but events array has {len(events)}"
        )


def _validate_case_info(case_info: dict, errors: list[str]) -> None:
    """Check required case_info fields."""
    required = [
        "case_number", "investigator", "device_user", "suspect",
        "storage_type", "storage_info", "seizure_time", "seizure_location",
    ]
    for field in required:
        if not case_info.get(field):
            errors.append(f"case_info.{field} is missing or empty")

    storage = case_info.get("storage_info", {})
    for sf in ("manufacturer", "model", "serial"):
        if not storage.get(sf):
            errors.append(f"case_info.storage_info.{sf} is missing or empty")


def _validate_signer_info(signer_info: dict, errors: list[str]) -> None:
    """Check required signer_info fields."""
    required = [
        "name", "email", "birth_date", "phone",
        "cert_fingerprint", "signature_image_hash",
    ]
    for field in required:
        if not signer_info.get(field):
            errors.append(f"signer_info.{field} is missing or empty")
