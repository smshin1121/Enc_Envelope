# UI/UX 현대화 + 성능 개선 통합 계획 (2026-07-10)

3자 교차 감사 결과 통합: Claude ui-qa-engineer(UI/UX), Claude perf-reviewer(성능), codex gpt-5.6-sol xhigh(독립 교차 감사).

## 1. 교차 대조 결과

### 1.1 양측 합의 (Claude ∩ codex) — 신뢰도 최상, 즉시 구현

| 항목 | Claude | codex | 우선순위 |
|------|--------|-------|---------|
| GCM 청크 전체 메모리 적재 → OOM (+ GCM 평문 한계 64GiB−32B 초과로 64GB 청크 동작 불가) | CRITICAL×2 | PERF-01 | **P0** |
| 봉인 1회당 원본 2~3회(재봉인 최대 4회) 풀 스캔 — collect_metadata 중복 | HIGH | PERF-02 | **P0** |
| 해시 청크 8KB — syscall 과다 | HIGH | PERF-03 | P1 |
| U4의 .enc SHA-256 계산이 비교 대상 없이 낭비 | HIGH | PERF-04 | P1 |
| U3/U4·R2가 메인 스레드 실행 → UI 프리즈 | (R2 fallback 순차 해싱) | PERF-04 | **P0** |
| 진행률/취소가 청크 단위 → 0%→100%, 취소 무반응 | HIGH | PERF-05 | **P0** |
| 대시보드 동기 쿼리(COUNT×4 + record_json 전체 파싱) | P1-6/MEDIUM | PERF-07, UI-07 | P1 |
| 웹 SMTP 동기 발송 + timeout 미지정 | HIGH | PERF-12 | P1 |
| PDF 렌더러 Jinja env/백엔드 판정 매회 반복 | MEDIUM | PERF-13 | P2 |
| Return 키 오동작 (Spinbox/Text 미가드, unseal/reseal 무조건 진행) | P0-5 | UI-02 | **P0** |
| 카드/스텝 인디케이터 키보드 접근 불가·포커스 링 부재 | P1-7 | UI-01 | P1 |
| DPI 미대응 (Windows 125~200% 배율 흐림) | P1-14 | UI-03 | P1 |
| success/warning 텍스트 WCAG 대비 미달 (~2.2:1) | P0-4 | UI-06 | **P0** |
| 웹 `lang="en"` + aria 부재 | W-2 | UI-08 | **P0** |
| 웹-데스크톱 브랜드 단절 (#533afd 미적용) | W-3 | UI-10 | P1 |
| 테마 우회 하드코딩 (색 40+곳, 폰트 58곳) | P1-1/P1-2 | UI-10 | P1 |
| 빈 상태/로딩 상태 미흡 | P1-9 | UI-07 | P1 |
| 다크모드 부재 (하드코딩 정리가 전제) | P2-1 | UI-05 | P2 |

### 1.2 Claude 단독 발견 — 코드 근거 확인됨, 구현 포함

- **P0**: 디버그 print 5곳(개인정보 콘솔 노출, seal_wizard.py:1233~), Toplevel Enter/Esc 바인딩 누수(위자드 파괴 후 TclError), 달력 연도 상한 2026 하드코딩(**현재 날짜 2026-07 기준 실사용 차단**), 웹 Bootstrap CDN 의존(폐쇄망 붕괴)
- **P1**: ToastManager 데드 코드(모달 messagebox 독점), 최근 이력 의사-테이블+미번역, 언어 전환이 홈만 갱신, StepIndicator 히트 반경 무한, S4/S7 ASCII 덤프 요약, 검증 오류 팝업 나열식, 토스트 고정 높이 겹침, 케이스 액션 바 8버튼 상시 노출
- **P2**: TSA 단일 스레드/과도한 재시도, resume 단위 64GB, reseal 크기 필터 없는 전량 해싱, `update()` 재진입 위험

### 1.3 codex 단독 발견 — 구현 포함

- **PERF-06**: 완료 콜백에서 PDF 생성이 메인 스레드 실행 → 100% 도달 직후 재프리즈
- **PERF-09**: Flask 목록 조회가 `SELECT *`로 PDF BLOB까지 적재
- **PERF-10**: auth_failures 인덱스 부재 — 인증 요청마다 대규모 스캔
- **PERF-11**: sync 엔드포인트 `MAX_CONTENT_LENGTH` 부재 + base64 이중 복사
- **PERF-14**: TSA용 PDF 해시 `read_bytes()` 전체 적재
- **UI-04**: 위자드 공통 스크롤 컨테이너 부재, 고정 폭 필드
- **UI-09**: 웹 테이블 `.table-responsive` 부재

### 1.4 불일치 → 판정

| 쟁점 | 판정 |
|------|------|
| Claude "S5는 백그라운드 스레드 OK" vs codex "U3/U4/R2 메인 스레드" | **양립** — 암·복호화(S5/U5)는 worker, 검증(U3/U4)·비교(R2)·PDF 생성은 메인 스레드. codex 지적 채택 |
| codex "100ms 폴링은 병목 아님" | Claude도 동일 — 콜백 **빈도 부족**(청크 단위)이 문제라는 데 합의 |
| codex "Flask N+1 없음" | 채택 — 문제는 N+1이 아니라 `SELECT *`/BLOB/무제한 목록 |

## 2. 구현 범위 (이번 작업)

### Wave 1 — 병렬 (파일 소유권 분리)
1. **백엔드 성능** (crypto/*, seal·unseal·reseal_process, sqlite_store, pdf_renderer, tsa): 스트리밍 GCM(포맷 불변), 1패스 해시 통합, 바이트 단위 진행률/취소, 해시 버퍼 8MiB, U4 낭비 해시 제거, 통계 단일 쿼리+인덱스, 단일 트랜잭션 저장, Jinja/백엔드 캐시, ThreadingHTTPServer
2. **웹** (src/web/*): Bootstrap 로컬 번들, lang="ko"+aria, 브랜드 CSS, SMTP timeout, MAX_CONTENT_LENGTH, auth_failures 인덱스, BLOB 제외 projection, 입력 타입, table-responsive
3. **GUI 기반** (theme, app, dashboard, case_manager, toast, i18n, main, case_detail_dialog): WCAG 텍스트 색, DPI 인식, 비동기 대시보드+로딩 상태, 토스트 연결, 키보드 접근, 빈 상태 CTA, 언어 전환 전 화면 적용, 최근 이력 테이블화

### Wave 2 — 위자드 (Wave 1의 theme/crypto 완료 후)
4. **위자드 UX** (seal/unseal/reseal_wizard, step_indicator, progress_dialog, widgets, signature_pad): 디버그 print 제거, 바인딩 누수 수정, 달력 연도 동적화, Return 가드, U3/U4/R2/PDF 생성 worker 이전, S4/S7 카드형 요약, 히트 반경, 인라인 에러 우선, 사전 계산 메타데이터 전달(1패스 연동)

### Wave 3 — 검증
5. ui-qa-engineer 통합 QA → pytest 회귀(기준선 272 passed, 2 skipped) → codex 변경분 교차 재검토

## 3. 장기 방안 (이번 미구현 — 로드맵)

| 항목 | 근거 | 제안 | 난이도 |
|------|------|------|--------|
| DB 비정규화: `unlock_time`, `file_count`, 상태 카운트 정수 컬럼 | 목록/만료 조회가 record_json 파싱에 의존 | `_ensure_case_columns` 마이그레이션 확장 + 저장 시 갱신 + 인덱스 | M |
| 케이스 목록 페이지네이션 | 수천 건에서 Treeview 전량 삽입 | keyset pagination + 페이지 로딩 | M |
| .enc 해시를 봉인지에 기록 → U4 실질 검증 | 현재 비교 대상 부재 | 기록지 스키마 v2 (논문 기술과 정합 검토 필요) | M |
| resume 세그먼트 축소(64GB→1~4GB) | 중단 시 최대 64GB 재작업 | 논문의 "64GB 구간" 기술과 상충 — 투고 심사 종료 후 결정 권장 | S |
| 재봉인 병렬 해싱 | hashlib GIL 해제로 4스레드 ~3배 | ThreadPoolExecutor(4) | M |
| sync 멀티파트 스트리밍 | base64 2배 메모리 | multipart 업로드 + 해시만 DB | M |
| 다크모드 | 하드코딩 정리(이번 구현) 후 가능 | 시맨틱 토큰 이중 팔레트 + prefers-color-scheme(웹) | L |
| 아이콘 세트 | 텍스트 온리 UI | PNG/PhotoImage 임베드 세트 | M |
| OTP 발송 큐 분리 | 요청 스레드 점유 | outbox 패턴 + worker | M |
