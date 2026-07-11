# Enc_Envelope ↔ research_site 연계 계약서

**버전**: 2026-07-11 (research_site 수신부 구현 완료 기준)
**대상**: Enc_Envelope 개발팀 (봉인 프로그램 + 웹)
**목적**: 봉인기록·키 조각을 research_site로 전달하기 위해 Enc_Envelope 측이 맞춰야 할 인터페이스 정의.
research_site 수신부는 모두 구현·배포됨. 아래는 **Enc_Envelope가 구현할 계약**이다.

> 📌 **공개용 사본**: 정본은 research_site(사설 레포)의 `docs/enc_envelope_sync_contract.md`이며,
> 이 문서는 운영 세부를 제거한 공개용 사본이다. 계약이 갱신되면 정본 기준으로 버전 날짜를 맞춘다.
> 인터페이스 계약만 담는다(시크릿 값·서버 내부 정보 없음). 서명 스킴·해시 산식 공개는 무방하며,
> 비밀은 `SYNC_SHARED_SECRET`·`SUBMIT_HASH_PEPPER` **값 자체**뿐이다 — 절대 레포에 커밋하지 말 것.

---

## 0. 전체 구조

- **research_site** = 수집 프런트(피압수자·수사관이 키 조각 업로드, 봉인기록 열람).
- **Enc_Envelope** = 봉인 주체(AES-256-GCM 봉인, SSS 키 분할, 봉인기록 JSON 생성).
- 연계 방향:
  1. **봉인기록(seal record)**: Enc_Envelope → research_site (`POST /api/seal-records`, HMAC 서명).
  2. **키 조각**: 사람이 research_site 웹에 업로드(피압수자=1번, 수사관=2번). Enc_Envelope는 조각 **형식 계약**만 맞추면 됨.
- 권장 아키텍처: research_site는 조각 수집 프런트, Enc_Envelope 웹 DB가 키 조각 단일 원장.

---

## 1. 공유 시크릿

- `SYNC_SHARED_SECRET`: 양측 환경변수로 배포하는 공유 비밀(HMAC 키).
- research_site 측에는 이미 설정되어 있음.
- **Enc_Envelope 측 env 에 동일 값**을 안전 채널로 전달받아 설정해야 연계가 작동한다.
- 시크릿 미설정 시 research_site는 `503` fail-closed.

---

## 2. 봉인기록 동기화 API — `POST /api/seal-records`

### 2.1 인증 — HMAC 서명 (replay 방지 포함)

요청마다 아래 3개 헤더 필수:

| 헤더 | 값 |
|---|---|
| `X-Sync-Timestamp` | 요청 시각, **unix epoch 초**(정수, UTC) |
| `X-Sync-Nonce` | 요청마다 **고유한 랜덤 문자열**. `^[A-Za-z0-9._-]{8,128}$` |
| `X-Sync-Signature` | 아래 정본 문자열의 HMAC-SHA256 (소문자 hex) |

**정본(canonical) 서명 문자열** — 이 바이트열에 서명한다:

```
canonical = str(timestamp).encode() + b"\n" + nonce.encode() + b"\n" + raw_body
X-Sync-Signature = hex( HMAC_SHA256(SYNC_SHARED_SECRET, canonical) )
```

- `raw_body` = HTTP 요청 본문 **바이트 그대로**(JSON 직렬화 결과, 재직렬화 금지).
- 파이썬 예:
  ```python
  import time, os, hmac, hashlib, secrets, requests
  ts = str(int(time.time()))
  nonce = "n-" + secrets.token_hex(16)
  raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
  canonical = ts.encode() + b"\n" + nonce.encode() + b"\n" + raw
  sig = hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()
  requests.post(URL, data=raw, headers={
      "Content-Type": "application/json",
      "X-Sync-Timestamp": ts, "X-Sync-Nonce": nonce, "X-Sync-Signature": sig})
  ```

### 2.2 서버 검증 규칙

1. 3개 헤더 누락/형식오류 → `401`.
2. `|서버시각 - X-Sync-Timestamp| > 300초` → `401` (시계 오차/replay 창 제한).
3. 정본 문자열 서명 불일치 → `401` (상수시간 비교).
4. `X-Sync-Nonce` **재사용** → `409` (replay 차단). 신선도 창 밖의 nonce는 서버가 자동 정리.
5. 시크릿 미설정 → `503`.

> **정당 재시도**: 같은 본문이라도 **새 nonce + 새 timestamp**로 다시 서명하면 통과한다(멱등 처리됨).
> **재전송(replay)**: 동일 nonce로 그대로 재전송하면 `409`.

### 2.3 본문 — 6필드 봉인기록 JSON (§1.3 스키마)

`Content-Type: application/json`. 아래 형식이어야 하며(그 외 `400`), 크기 상한 **8MB**(초과 `413`).

```jsonc
{
  "seal_id": "S-20260710-69A071",         // ^S-\d{8}-[0-9A-F]{6}$ (대문자 hex 6)
  "case_info": {
    "case_number":       "2026-형제-1234",
    "suspect":           "홍길동",          // 피압수자명
    "device_user":       "홍길동 갤럭시",
    "seizure_location":  "서울시 종로구 ...",
    "seizure_time":      "2026-07-10 14:30:00",
    "investigator":      "김수사"
  },
  "process_info": {
    "seal_type":   "Sealing",              // Sealing | Unsealing | Resealing
    "start_time":  "2026-07-10T14:30:00Z", // ISO8601 권장(Z=UTC)
    "end_time":    "2026-07-10T14:35:00Z",
    "algorithm":   "AES-256-GCM",
    "unlock_time": "2026-08-10T00:00:00Z"  // (선택) 시간기반 접근제어 — §4 참고
  },
  "file_info": {
    "original_files": [ {"filename": "a.txt", "md5": "...", "sha256": "..."} ],
    "result_files":   [ {"filename": "a.txt.enc", "md5": "...", "sha256": "..."} ]
    // original_files[i] ↔ result_files[i] 를 같은 인덱스로 대응
  },
  "signer_info": {
    "name":       "홍길동",                // 서명자(=피압수자) 성명
    "birth_date": "1990-01-01",            // 또는 19900101 — 숫자만 추출해 YYYYMMDD
    "phone":      "010-9999-8888",         // 숫자만 추출해 사용
    "email":      "hong@example.com"
    // ⚠ 성별(gender) 필드는 스키마에 없음 — subject 해시에도 미사용(§3)
  },
  "history": {
    "summary": "S1U0R0",                   // S{봉인}U{해제}R{재봉인} 카운트
    "events": [
      {"event_id": "EVT-0001", "seal_type": "Sealing", "start_time": "...", "investigator": "..."}
      // 이번에 전송하는 사건의 '현재 이벤트'가 events 의 마지막 항목이어야 함
    ]
  },
  "record_pdf": "<base64>"                 // (선택) 봉인기록지 PDF. %PDF- 로 시작, 8MB 이하
}
```

**필수 필드(누락 시 `422` 거부)**:
- `seal_id` (형식 불일치도 `422`).
- `signer_info.birth_date` 및 `signer_info.phone` — 이 둘이 비면 subject 해시가 빈 값이 되어 거부.

### 2.4 멱등성

- `(seal_id, history.events[-1].event_id)` 기준. 같은 조합 재수신은 **성공으로 무시**(중복 저장 안 함).
- 따라서 각 이벤트는 **고유한 `event_id`**를 부여할 것. `prev_event_ref`는 research_site가 `events[-2].event_id`로 자동 연결.

### 2.5 응답 (항상 JSON `{"status": "...", "message": "..."}`)

| 코드 | 의미 |
|---|---|
| `200` | 저장 성공(또는 멱등 무시). `message`에 `record_pdf` 저장 여부 포함 |
| `400` | JSON 파싱 실패 / 6필드 스키마 아님 |
| `401` | 서명·타임스탬프·nonce 인증 실패 |
| `409` | nonce 재사용(replay) |
| `413` | 본문 8MB 초과 |
| `422` | 필수 필드 누락 / seal_id 형식 오류 등 저장 거부 |
| `503` | 시크릿 미설정 등 서버 설정 오류(fail-closed) |

> 참고: 사람이 수동 업로드하는 `POST /upload_seal_json`(multipart, `json_file`) 경로도 있으나, **서버간 연계는 반드시 `/api/seal-records`(HMAC)를 사용**한다.

---

## 3. subject_ref_hash — 동일인 결속 규칙 (매우 중요)

research_site는 봉인기록·피압수자 제출·사건 정보를 **subject_ref_hash**로 동일인 결속한다.
Enc_Envelope의 `signer_info`가 **피압수자 제출 정보와 같은 값**을 만들어야 조각 업로드가 허용된다(§5 F6).

**산식**:
```
birth8       = (birth_date 에서 숫자만) → 8자리 YYYYMMDD
                 (6자리면 세기 추정: 성별 1,2→19 / 3,4→20. 단 서명자엔 성별 없어 8자리 권장)
phone_digits = (phone 에서 숫자만)         // 예: "010-9999-8888" → "01099998888"
subject_ref_hash = HMAC_SHA256( SUBMIT_HASH_PEPPER, (birth8 + phone_digits).encode() )
```

- ⚠ **성별(gender)은 해시에 넣지 않는다**(봉인 JSON에 성별이 없으므로). birth+phone만으로 결속.
- `SUBMIT_HASH_PEPPER`는 research_site 내부 비밀(연계에 노출 불필요). Enc_Envelope는 **birth_date·phone을 피압수자 본인 값과 일치**시키기만 하면 된다.
- 즉 **같은 사람이면 signer_info(birth_date, phone)와 피압수자 제출(생년월일, 휴대폰)이 같은 숫자열**이어야 한다.

---

## 4. unlock_time — 시간기반 접근제어 (선택)

- `process_info.unlock_time`(또는 `case_info.unlock_time`)을 주면, research_site는 **그 시각 이전의 키 복원을 차단**한다.
- 판정은 **UTC** 기준 → ISO8601 `...Z`(UTC)로 보낼 것.
- 봉인/재봉인이 여러 번이면 research_site는 원장의 **MAX(unlock_time)**을 적용(가장 늦은 잠금이 유지 = 조기 복원 방지).
- 미지정이면 즉시 잠금해제(복원 가능).

---

## 5. SSS 키 조각 형식 계약

키 조각은 **사람이** research_site 웹에 업로드하지만(피압수자/수사관), Enc_Envelope가 생성하는 조각 파일이 아래 형식을 지켜야 한다.

- 라이브러리: **`secretsharing.SecretSharer`** (`PlaintextToHexSecretSharer` 아님 — 무증상 오답 복원 위험).
- 생성: `SecretSharer.split_secret(hex_key, 2, 4)` — `hex_key`는 AES-256 키의 **64자 소문자 hex**.
- 조각 문자열: **`N-<hex>`** (N=1..4, 인덱스 내장). 정규식 `^[1-4]-[0-9a-fA-F]+$`.
- 인덱스 의미: **1=피압수자, 2=수사관, 3=시스템, 4=관리자**. 웹에는 1·2만 올라온다.
- 조각 파일: **UTF-8 텍스트 한 줄, 4KB 이하**. 확장자 `.share`.
- 복원: `SecretSharer.recover_secret([조각A, 조각B])` — **서로 다른 인덱스 2개**. 결과는 `zfill(64)` 후 `^[0-9a-f]{64}$` 검증.

---

## 6. 운영 순서 (F6 — 반드시 준수)

research_site는 **봉인기록이 먼저 등록된 봉인**의 조각만 받는다(도용 방지, fail-closed).

1. Enc_Envelope가 봉인 → **봉인기록 JSON을 `/api/seal-records`로 먼저 전송**(seal_id + signer_info 포함).
2. 그 후 피압수자·수사관이 해당 `seal_id`로 키 조각을 웹 업로드.
   - 미등록 seal_id로 업로드 시 → 거부("봉인 정보가 등록되어 있지 않습니다").
   - 봉인기록의 subject(birth+phone)와 업로더 본인확인 정보가 다르면 → 거부("본인 명의가 아닙니다").

> 즉 **봉인기록 등록 → 조각 업로드** 순서가 강제된다.

---

## 7. Enc_Envelope 구현 체크리스트

- [ ] `SYNC_SHARED_SECRET`을 안전 채널로 수령 → env 설정.
- [ ] `POST /api/seal-records` 호출부 구현: §2.1 정본 서명 + 3헤더(`X-Sync-Timestamp/Nonce/Signature`).
- [ ] 요청마다 **고유 nonce**, **현재 unix epoch timestamp** 생성.
- [ ] 본문을 §2.3 6필드 스키마로 직렬화. `raw_body`에 서명하고 **그 바이트 그대로** 전송(재직렬화 금지).
- [ ] 각 이벤트에 **고유 `event_id`** 부여(멱등 키).
- [ ] `signer_info.birth_date·phone`을 피압수자 본인 값과 **동일 숫자열**로(§3).
- [ ] 조각 생성 시 `SecretSharer.split_secret` + `N-<hex>` 형식(§5).
- [ ] 운영: **봉인기록 등록 → 조각 업로드** 순서 준수(§6).
- [ ] (선택) 시간기반 접근제어 필요 시 `unlock_time`을 UTC(ISO `...Z`)로 포함(§4).
- [ ] (선택) `record_pdf`(base64, `%PDF-` 시작, 8MB 이하) 첨부.

---

## 부록 A. 최소 요청 예시 (파이썬)

```python
import time, secrets, hmac, hashlib, json, requests

SECRET = "<SYNC_SHARED_SECRET>"
URL = "https://<research_site>/api/seal-records"

payload = {
  "seal_id": "S-20260710-69A071",
  "case_info": {"case_number": "2026-형제-1234", "suspect": "홍길동",
                "device_user": "홍길동 갤럭시", "seizure_location": "서울...",
                "seizure_time": "2026-07-10 14:30:00", "investigator": "김수사"},
  "process_info": {"seal_type": "Sealing", "start_time": "2026-07-10T14:30:00Z",
                   "end_time": "2026-07-10T14:35:00Z", "algorithm": "AES-256-GCM"},
  "file_info": {"original_files": [{"filename": "a.txt", "md5": "..", "sha256": ".."}],
                "result_files":   [{"filename": "a.txt.enc", "md5": "..", "sha256": ".."}]},
  "signer_info": {"name": "홍길동", "birth_date": "1990-01-01",
                  "phone": "010-9999-8888", "email": "hong@example.com"},
  "history": {"summary": "S1U0R0",
              "events": [{"event_id": "EVT-0001", "seal_type": "Sealing"}]}
}

raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
ts = str(int(time.time()))
nonce = "n-" + secrets.token_hex(16)
canonical = ts.encode() + b"\n" + nonce.encode() + b"\n" + raw
sig = hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()

r = requests.post(URL, data=raw, headers={
    "Content-Type": "application/json",
    "X-Sync-Timestamp": ts, "X-Sync-Nonce": nonce, "X-Sync-Signature": sig})
print(r.status_code, r.json())
```
