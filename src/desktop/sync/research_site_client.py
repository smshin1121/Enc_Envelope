"""research_site 봉인기록 푸시 클라이언트.

계약: ``docs/research-site-sync-contract.md`` (§2 — POST /api/seal-records).

HMAC 서명::

    canonical = str(timestamp) + "\\n" + nonce + "\\n" + raw_body   (바이트)
    X-Sync-Signature = hex( HMAC_SHA256(SYNC_SHARED_SECRET, canonical) )

환경변수 (둘 다 필수 — 미설정이면 ``push_seal_record_safe`` 는 스킵):

    RESEARCH_SITE_URL    수신 서버 베이스 URL (예: ``https://example.org:1643``)
    SYNC_SHARED_SECRET   공유 비밀. 절대 커밋하지 말 것 — 안전 채널로만 전달.

CLI (수동/백필 푸시)::

    python -m desktop.sync.research_site_client record.json [--pdf record.pdf]

표준 라이브러리만 사용한다 (추가 의존성 없음).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

API_PATH = "/api/seal-records"
DEFAULT_TIMEOUT = 15

ENV_BASE_URL = "RESEARCH_SITE_URL"
ENV_SECRET = "SYNC_SHARED_SECRET"


class ResearchSiteSyncError(RuntimeError):
    """research_site 푸시 실패 (설정 누락·네트워크·서버 거부)."""


def _canonical_signature(secret: str, timestamp: str, nonce: str, raw_body: bytes) -> str:
    canonical = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw_body
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def _prepare_payload(
    record_json: str | bytes,
    record_pdf_path: Optional[str] = None,
) -> bytes:
    """전송 본문 생성.

    - 데스크탑 기록의 최상위 ``unlock_time_iso`` 를 계약 위치인
      ``process_info.unlock_time`` 으로 복사한다 (원본 키는 유지 — 수신측은 무시).
    - ``record_pdf_path`` 가 주어지면 base64 로 ``record_pdf`` 필드에 첨부한다.
    """
    if isinstance(record_json, bytes):
        record_json = record_json.decode("utf-8")
    data = json.loads(record_json)
    if not isinstance(data, dict):
        raise ResearchSiteSyncError("봉인기록 JSON 최상위가 객체가 아닙니다.")

    unlock_iso = data.get("unlock_time_iso")
    if unlock_iso:
        process_info = data.setdefault("process_info", {})
        if isinstance(process_info, dict) and not process_info.get("unlock_time"):
            process_info["unlock_time"] = unlock_iso

    if record_pdf_path:
        with open(record_pdf_path, "rb") as f:
            pdf_bytes = f.read()
        if not pdf_bytes.startswith(b"%PDF-"):
            raise ResearchSiteSyncError(
                f"PDF 시그니처(%PDF-)가 아닙니다: {record_pdf_path}"
            )
        data["record_pdf"] = base64.b64encode(pdf_bytes).decode("ascii")

    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def push_seal_record(
    record_json: str | bytes,
    record_pdf_path: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    secret: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """봉인기록을 research_site 로 푸시한다.

    Returns:
        서버 응답 JSON (``{"status": ..., "message": ...}``).

    Raises:
        ResearchSiteSyncError: 설정 누락, 네트워크 오류, 2xx 아닌 응답.
    """
    base_url = base_url or os.environ.get(ENV_BASE_URL, "").strip()
    secret = secret or os.environ.get(ENV_SECRET, "").strip()
    if not base_url or not secret:
        raise ResearchSiteSyncError(
            f"{ENV_BASE_URL}/{ENV_SECRET} 환경변수가 설정되지 않았습니다."
        )

    raw_body = _prepare_payload(record_json, record_pdf_path)
    timestamp = str(int(time.time()))
    nonce = "n-" + secrets.token_hex(16)
    signature = _canonical_signature(secret, timestamp, nonce, raw_body)

    request = urllib.request.Request(
        base_url.rstrip("/") + API_PATH,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Sync-Timestamp": timestamp,
            "X-Sync-Nonce": nonce,
            "X-Sync-Signature": signature,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        raise ResearchSiteSyncError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ResearchSiteSyncError(f"전송 실패: {exc}") from exc

    logger.info("research_site 푸시 성공: %s", body.get("message", body))
    return body


def push_seal_record_safe(
    record_json: str | bytes,
    record_pdf_path: Optional[str] = None,
    **kwargs: Any,
) -> bool:
    """예외를 던지지 않는 푸시 (봉인/해제 파이프라인용).

    연계 미설정이면 조용히 스킵하고, 실패는 경고 로그만 남긴다.
    로컬 봉인 절차는 원격 연계와 무관하게 항상 완결되어야 한다.
    """
    if not (
        (kwargs.get("base_url") or os.environ.get(ENV_BASE_URL, "").strip())
        and (kwargs.get("secret") or os.environ.get(ENV_SECRET, "").strip())
    ):
        logger.info("research_site 연계 미설정 — 원격 푸시 스킵")
        return False
    try:
        push_seal_record(record_json, record_pdf_path, **kwargs)
        return True
    except Exception as exc:
        logger.warning("research_site 푸시 실패 (로컬 기록은 보존됨): %s", exc)
        return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="봉인기록 JSON 을 research_site 로 수동 푸시 (백필용)."
    )
    parser.add_argument("record", help="봉인기록 JSON 파일 경로")
    parser.add_argument("--pdf", help="첨부할 봉인기록지 PDF 경로", default=None)
    parser.add_argument("--url", help=f"베이스 URL (기본: ${ENV_BASE_URL})", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with open(args.record, encoding="utf-8") as f:
        record_json = f.read()
    try:
        body = push_seal_record(record_json, args.pdf, base_url=args.url)
    except ResearchSiteSyncError as exc:
        logger.error("푸시 실패: %s", exc)
        return 1
    print(json.dumps(body, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
