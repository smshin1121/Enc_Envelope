# E01(EWF) 저장 옵션 추가 타당성 검토 보고서

**대상 시스템**: 디지털증거 전자봉인시스템 (Enc_Envelope)
**작성일**: 2026-07-10 | **성격**: 사전 타당성 검토 (구현 없음)

## 1. 요약 (결론 먼저)

- **E01 "쓰기(생성)" 기능의 전면 도입은 권고하지 않는다.** 개별 파일 봉인이라는 시스템 본질과 E01의 물리 이미지 컨테이너 성격이 불일치하고, 유일한 현실적 오픈소스 쓰기 경로(pyewf write)가 실험적·저신뢰 상태이며, 현 개발환경(Python 3.13.2)에는 바이너리 휠조차 없다.
- 대신 **"E01 입력 인식·검증(읽기 전용)" 기능을 1단계로 권고**한다. 실무에서 E01은 이미 EnCase/FTK/MD 시리즈로 현장에서 생성되므로, 본 시스템이 E01을 "생성"하는 것보다 **외부에서 생성된 E01을 봉인 대상으로 받아 내부 취득 해시(MD5/SHA1)를 검증·봉인기록지에 병기**하는 것이 법정 증명력과 실무 정합성 양쪽에서 이득이 크고 리스크가 낮다.
- 개별 파일 수집에 의미론적으로 맞는 컨테이너는 E01이 아니라 **L01(논리 증거 파일)**인데, libewf는 L01/Lx01을 **읽기 전용**으로만 지원한다(쓰기는 "장기 계획"). 오픈소스로 L01을 생성할 방법이 현재 없다는 점이 이 검토의 결정적 제약이다.

## 2. 기술 옵션 조사 (2026-07 기준)

### 2.1 libewf / pyewf (libewf-python)

| 항목 | 조사 결과 |
|---|---|
| pip 설치 (Windows) | `libewf-python` PyPI 배포. **최신 20240506 (2024-05-06)**. Windows 휠: cp38–cp312 (win32/win_amd64). **cp313 휠 없음** — 본 검토 환경 Python 3.13.2에서 실측 확인: `--only-binary :all:` 다운로드 실패 → 소스 빌드(MSVC) 필요 |
| 포맷 지원 | 읽기+쓰기: **E01, Ex01, S01** / 읽기 전용: **L01, Lx01** (쓰기는 장기 계획) / **Ex01 암호화: 미지원**, Ex01 bzip2: WIP |
| 쓰기 실질 수준 | C 라이브러리(ewfacquire/ewfacquirestream)의 쓰기 경로는 검증됨. 그러나 **pyewf(Python 바인딩)의 쓰기 API는 문서화가 사실상 없고**(libyal 위키는 읽기 예제만 제공), 쓰기 시 deflate 압축 누락 오류 보고(libewf#162, 응답 없음) 등 실사용 사례가 희박 |
| 라이선스 | **LGPL-3.0-or-later** (도구 일부 GPL-3.0). pip 동적 사용은 문제 없음. 배포판에 정적 번들 시 LGPL 의무 발생 |
| 유지보수 | Joachim Metz(libyal) 단독 유지. 커밋은 지속되나 프로젝트 스스로 **"experimental"** 표기. PyPI 릴리스 주기 연 0~2회 |

### 2.2 대안 라이브러리·도구

| 대안 | 평가 |
|---|---|
| pyaff4 (AFF4) | Google/Schatz Forensic 소유. **쓰기 지원 "currently broken"** 명시, 릴리스 정체. AFF4 표준 자체는 암호화·논리 수집(AFF4-L) 지원으로 이상적이나 **구현체가 미성숙** |
| dfimagetools (libyal) | 이미지에서 파일 **추출** 도구 — E01 생성 불가. 해당 없음 |
| miniEwf 등 순수 Python | 읽기 전용 실험 구현. 해당 없음 |
| FTK Imager CLI / EnCase / X-Ways 연동 | E01/L01 생성은 확실하나 상용·재배포 제한, 외부 바이너리 의존은 "자족적 봉인 시스템" 주장과 충돌. 실무상 이미 현장에서 병용되므로 굳이 내장할 이유 약함 |
| ewfacquirestream 서브프로세스 | 기술적으로 유일하게 안정적인 오픈소스 E01 쓰기 경로(stdin→E01). 단 **libewf 공식 Windows 바이너리 미배포** — 직접 빌드·번들 필요 |
| VHD / DD+해시 | 쓰기 트리비얼하나 포렌식 취득 메타데이터(examiner, case no., 청크 CRC) 없음 — "표준 포렌식 포맷" 채택 목적 소실 |

### 2.3 포맷 정합성 요약

| 포맷 | 성격 | 암호화 | 오픈소스 쓰기 | 개별 파일 봉인과의 정합 |
|---|---|---|---|---|
| E01 | 물리/비트스트림 이미지 | 없음 | 가능(불안정) | **낮음** — 단일 파일을 raw 스트림으로 감싸면 분석도구에서 "파일시스템 없는 blob"으로 보임 |
| Ex01 | EnCase 7+ 이미지 | 있음(독점) | libewf 쓰기 가능하나 **암호화 미지원** | 낮음 |
| **L01/Lx01** | **논리 증거(개별 파일+메타)** | 없음 | **불가(읽기 전용)** | **높음 — 그러나 생성 수단 부재** |
| AFF4 / AFF4-L | 오픈 표준, 암호화·논리 수집 | 있음 | pyaff4 쓰기 고장 | 표준상 최적, 구현체 미성숙 |

## 3. 아키텍처 정합성 분석

현 파이프라인(`src/desktop/seal_process.py`): S1 `encrypt_file`(AES-256-GCM 스트리밍, 청크별 nonce/tag, MD5/SHA-256 단일 패스) → S4 봉인기록 JSON → S5 PAdES+TST PDF → S6 SSS 2-of-4 → S7 DB 저장.

### 삽입 지점 후보 평가

- **(a) 원본 → E01/L01 수집 → 그 컨테이너를 .enc로 봉인**: 논리적으로 유일하게 순서가 맞는 방안(E01은 암호화 미지원이므로 반드시 안쪽 계층). 단, 단일 파일의 E01화는 의미가 약하고, L01화는 수단이 없다. **실무적 통찰: 이 지점은 이미 열려 있다** — 수사관이 EnCase/FTK로 만든 E01 파일을 S1의 `source_file`로 지정하면 현재 코드가 그대로 봉인한다. 부족한 것은 "생성"이 아니라 **입력이 E01임을 인식하고 내부 취득 해시를 검증·기록하는 기능**이다.
- **(b) .enc를 E01에 담기**: **기각.** 암호문은 비압축성이라 EWF 압축 무의미, 분석도구가 해석 불가, E01의 CRC/MD5는 GCM 인증 태그와 완전 중복.
- **(c) 봉인해제 시 복원 산출물을 E01/L01로 export**: L01이 정합하나 쓰기 불가. E01 raw-wrap export는 가능하지만 수신 측이 EnCase에서 직접 L01을 만드는 것이 더 자연스러움. 우선순위 낮은 선택 옵션.

### 무결성 계층 관계 — 중복이 아니라 보완

| 계층 | 무엇을 증명 | 시점 |
|---|---|---|
| E01 내부 (32KiB 청크 CRC + 취득 MD5/SHA1) | **매체 내용**이 취득 시점 이후 불변 | 현장 취득 시 |
| 봉인기록의 MD5/SHA-256 (`file_info.original_files`) | **컨테이너 파일 자체**가 봉인 시점 이후 불변 | 봉인 시 |
| GCM 청크 태그 + PAdES + RFC 3161 TST | 암호문 위변조 탐지 + 기록의 서명·시점 확정 | 봉인~해제 전 구간 |

세 계층이 "취득 → 봉인 → 개봉"의 연속성(chain of custody)을 각각 다른 구간에서 담보하므로 **법정 제출 시 상호 보강**된다. 봉인해제 후 pyewf로 E01 내부 해시까지 재검증하면 "개봉 후 매체 내용 동일성"을 표준 도구와 교차 검증 가능 — 이것이 E01 연동의 실질적 법적 이점.

### 기밀성 충돌 정리

E01은 암호화 미지원, Ex01 암호화는 EnCase 독점(libewf 미지원), AFF4 암호화는 구현체 부재. 따라서 **기밀성은 어떤 경우에도 현행 .enc(AES-256-GCM) 계층이 담당해야 하며, E01은 항상 .enc의 안쪽**에 위치해야 한다. "E01로 저장" 옵션이 .enc를 대체하는 형태는 봉인의 기밀성 요구를 위반하므로 배제.

## 4. 법적·실무 관점 (한국)

- 대검찰청 「디지털 증거의 수집·분석 및 관리 규정」(예규, 2024-10-01 개정)은 **해시값 확인·봉인·참여권**을 요구할 뿐 특정 컨테이너 포맷을 강제하지 않는다. 판례(일심회 2007도7257, 왕재산 2013도2511 계열)도 해시 동일성·보관 연속성·참여를 기준으로 하지 포맷을 다루지 않는다. → **E01 자체가 증거능력 요건은 아니다.**
- 다만 E01은 국내 수사기관과 EnCase/FTK/Autopsy/X-Ways 전반의 **사실상 표준**이므로, E01 입력 검증·해시 병기는 후속 분석 단계와의 인수인계 신뢰성을 높인다.
- **논문(ICT Express 투고본) 관계**: 본 시스템의 기여는 참여권 보장 봉인 절차 모델이며 컨테이너 포맷과 직교한다. E01 쓰기를 넣어도 기여 주장이 강화되지 않는다. 반면 "표준 포렌식 이미지 포맷(E01)과의 상호운용 — 취득 해시를 봉인기록에 승계하여 취득~개봉 전 구간의 연속 무결성 사슬 구성"은 실용성 절에 한 단락으로 기여를 **보강**할 수 있다.

## 5. 권고안

### 옵션 비교

| 옵션 | 내용 | 효익 | 난이도 | 리스크 |
|---|---|---|---|---|
| A. E01 생성 내장 (쓰기) | 봉인 전 원본을 E01로 수집 후 .enc | 낮음 (현장 도구와 중복) | 높음 (pyewf 쓰기 불안정, cp313 휠 부재, Windows 바이너리 자체 빌드) | **높음** — 실험적 쓰기 경로가 증거 컨테이너를 만드는 것 자체가 법정 리스크 |
| **B. E01 인식·검증 (읽기)** | E01 입력 감지 → 내부 취득 해시/메타 검증 → 봉인기록 병기, 해제 시 재검증 | **높음** (연속 무결성 사슬, 실무 수용성, 논문 보강) | **낮음** (pyewf 읽기는 성숙, cp311/312 휠 존재) | 낮음 (Python 3.13 휠 부재 — 3.12 고정 또는 순수 Python 헤더 파서로 회피) |
| C. 해제 시 E01/L01 export | 복원 산출물을 컨테이너로 출력 | 중 (인수인계 편의) | L01: **불가**, E01 raw-wrap: 중 | 중 (raw-wrap E01의 해석 한계 고지 필요) |

### 단일 권고

**옵션 B (E01 읽기 전용 인식·검증) 채택.** A는 보류(libewf L01 쓰기 지원 또는 AFF4-L 구현 성숙 시 재검토), C는 2단계 선택 기능.

### 단계적 구현 스케치 (참고용, 미구현)

- **Phase 1 (~2일, 신규 약 300~400라인)**
  - 신규 `src/desktop/forensic/ewf_reader.py`: E01 시그니처(`EVF\x09\x0d\x0a\xff\x00`) 감지, 세그먼트 글롭, 취득 메타데이터(examiner/case/notes)와 stored MD5/SHA1 추출, verify. pyewf 가용 시 사용, 불가 시 헤더 섹션 한정 순수 Python 폴백
  - `seal_process.py` S1/S4 확장: `file_info.original_files[]`에 `container_format: "EWF-E01"`, `acquisition_md5`, `acquisition_metadata` optional 필드 (스키마 하위호환)
  - `unseal_process.py` U4~U5: 복원 파일이 E01이면 내부 해시 재검증 결과를 봉인해제기록지에 추가
  - 의존성: `libewf-python`(optional extra; Python 3.12 권장 명시)
- **Phase 2 (선택, ~3일)**: 봉인해제 마법사 "E01 export" 체크박스 — ewfacquirestream 번들 또는 pyewf 쓰기(사전 검증 필수). L01 export는 libewf 쓰기 지원 전까지 미제공 명시
- **테스트**: FTK Imager 생성 소형 E01 픽스처 단위 테스트, E01 봉인→해제→내부 해시 재검증 통합 테스트

### 주요 리스크·제약

1. **Python 3.13.2 환경에 libewf-python 휠 부재** (실측) — 3.12 병행 환경, 소스 빌드, 차기 릴리스 대기, 또는 검증 한정 순수 Python 파서 중 택일
2. libewf "experimental" 단독 유지보수 — 읽기 경로는 Plaso/Autopsy 등 대규모 하방 사용으로 사실상 검증, 쓰기 경로는 아님
3. LGPL-3.0+ — pip 동적 사용 무방, 단일 실행파일 패키징 시 고지 의무 확인

## 출처

- libyal/libewf GitHub — 포맷별 읽기/쓰기 지원, Ex01 암호화 미지원, experimental 상태: https://github.com/libyal/libewf/
- libewf-python PyPI — 20240506, cp38–cp312 Windows 휠, LGPLv3+: https://pypi.org/project/libewf-python/
- forensics.wiki Libewf — L01/Lx01 읽기 전용: https://forensics.wiki/libewf/
- libewf issue #162 — pyewf 쓰기 시 deflate 미지원: https://github.com/libyal/libewf/issues/162
- libyal wiki Python development (읽기 예제만 존재): https://github.com/libyal/libewf/wiki/Python-development
- aff4/pyaff4 — 쓰기 지원 고장 명시: https://github.com/aff4/pyaff4
- 국가법령정보센터 — (대검찰청) 디지털 증거의 수집·분석 및 관리 규정: https://www.law.go.kr/LSW//admRulInfoP.do?admRulSeq=2100000211619&chrClsCd=010201
- 한국형사·법무정책연구원 — 형사증거법상 디지털 증거의 증거능력: https://www.kicj.re.kr/boardDownload.es?bid=0003&list_no=9286&seq=5
- Forensic Focus — L01 논리 증거 포맷 논의: https://www.forensicfocus.com/forums/general/logical-evidence-format-l01-file/
