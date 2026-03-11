# KIS Open API 연동 가이드

> TradeMachine 프로젝트에서 한국투자증권 API를 연동하기 위한 전체 과정을 단계별로 정리한 문서.
> **역할:** 인증 플로우, 에러 코드, httpx 호출 패턴의 기준(SoT) 문서.
>
> 관련 문서:
> - 프로젝트 전체 계획 & .env 마스터: [project-plan.md](./project-plan.md)
> - API 엔드포인트/TR ID 레퍼런스: [kis-api-reference.md](./kis-api-reference.md)
> - 시스템 플로우 상세: [system-flow.md](./system-flow.md)

---

## 1. 사전 준비

### 1.1 필요한 것

| 항목 | 설명 |
|------|------|
| 한국투자증권 계좌 | 모바일 앱에서 비대면 개설 가능 (신분증 필요) |
| 모의투자 계좌 | 실제 돈 없이 테스트용. 별도 신청 필요 |
| 이메일 등록 | 한국투자증권 앱 > 고객 서비스 > 주소 연락처 수정에서 이메일 등록 |
| KIS Developers 서비스 신청 | API 사용을 위한 서비스 가입 |

### 1.2 계좌 개설

1. **한국투자증권** 모바일 앱 다운로드
2. 비대면 계좌개설 진행 (주민등록증 또는 운전면허증)
3. 계좌 개설 완료 후 **계좌번호** 메모
   - 형식: `50012345-01` → 앞 8자리(`50012345`) + 뒤 2자리(`01`)

### 1.3 모의투자 신청

실전 투자 전에 반드시 모의투자로 테스트해야 한다.

1. 한국투자증권 웹사이트 로그인
2. **트레이딩 > 모의투자 > 주식/선물옵션 모의투자** 이동
3. 모의투자 신청
4. **나의계좌** 탭에서 모의투자 계좌번호 확인 후 저장

---

## 2. KIS Developers 서비스 신청 & 앱키 발급

### 2.1 서비스 신청

1. [KIS Developers 포털](https://apiportal.koreainvestment.com/) 접속
2. **API 신청** 버튼 클릭
3. 스마트폰 인증으로 로그인
4. 약관 동의
5. **KIS Developers 서비스 신청하기** 창에서:
   - 계좌 선택 (모의투자 계좌 선택)
   - API 그룹의 **모든 체크박스 선택** (주문, 조회, 체결 조회 등)
   - 스마트폰 인증 진행
6. 신청 완료

### 2.2 앱 등록 & 키 발급

1. KIS Developers 포털 > **Application > 앱등록**
2. 앱 이름, 설명 작성
3. 등록 완료 후 **Application 목록**에서 확인:

| 발급 항목 | 설명 | 예시 |
|-----------|------|------|
| `APP_KEY` | 클라이언트 ID (공개키 역할) | `PSxxxxxxxxxxxxxxxxxxx` |
| `APP_SECRET` | 클라이언트 시크릿 (비밀키 역할) | `Rh0xxxxxxxxxxx...` |

### 2.3 중요: 모의투자 vs 실전투자 키는 별도

| 구분 | 도메인 | APP_KEY | APP_SECRET |
|------|--------|---------|------------|
| 모의투자 | `https://openapivts.koreainvestment.com:29443` | 모의투자용 별도 발급 | 모의투자용 별도 발급 |
| 실전투자 | `https://openapi.koreainvestment.com:9443` | 실전투자용 별도 발급 | 실전투자용 별도 발급 |

- 모의투자 키로 실전투자 도메인 호출 시 **에러 발생**
- 반드시 환경에 맞는 키를 사용해야 함
- 우리 프로젝트에서는 `.env` 파일에서 환경을 분리하여 관리

---

## 3. 프로젝트 환경 설정 (.env)

> 전체 설정값은 **[project-plan.md §7 설정 관리](./project-plan.md)** 에서 3계층으로 관리.
> 이 섹션에서는 KIS API 연동에 필요한 인증 관련 설정만 설명한다.

### 3.1 KIS 연동에 필요한 핵심 설정

| 설정키 | 예시 값 | 용도 |
|--------|---------|------|
| `KIS_APP_KEY` | `PSxxxxxxxxxxxxxxxxxxx` | 발급받은 앱 키 |
| `KIS_APP_SECRET` | `Rh0xxxxxxxxxxxxxxxxxxxxxxx` | 발급받은 앱 시크릿 |
| `KIS_CANO` | `50012345` | 계좌번호 앞 8자리 |
| `KIS_ACNT_PRDT_CD` | `01` | 계좌번호 뒤 2자리 |
| `KIS_BASE_URL` | `https://openapivts.koreainvestment.com:29443` | 모의/실전 도메인 |
| `KIS_IS_PAPER_TRADING` | `true` | 모의투자 여부 (TR ID 분기 기준) |

### 3.2 계좌번호 분리 규칙

계좌번호 `50012345-01`은 KIS API에서 항상 두 필드로 분리해서 보내야 한다:

| 필드 | 값 | 설명 |
|------|-----|------|
| `CANO` | `50012345` | 종합계좌번호 앞 8자리 |
| `ACNT_PRDT_CD` | `01` | 계좌상품코드 뒤 2자리 |

---

## 4. 인증 플로우 상세

### 4.1 Access Token 발급

모든 REST API 호출의 전제 조건. 앱 시작 시 1번 발급하고 캐싱한다.

```
POST {BASE_URL}/oauth2/tokenP
Content-Type: application/json

{
  "grant_type": "client_credentials",
  "appkey": "{APP_KEY}",
  "appsecret": "{APP_SECRET}"
}
```

**응답:**
```json
{
  "access_token": "eyJ0eXAiOiJKV1...",
  "token_type": "Bearer",
  "expires_in": 86400,
  "access_token_token_expired": "2026-03-12 10:00:00"
}
```

**토큰 관리 정책:**

| 항목 | 값 |
|------|-----|
| 유효기간 | 약 24시간 (응답의 `expires_in` 참고) |
| 발급 제한 | **1분에 1회**. 초과 시 `EGW00133` 에러 |
| 갱신 전략 | 만료 1시간 전에 선제 갱신 |
| 저장 방식 | 메모리 캐시 (재시작 시 재발급) |

> **주의:** 토큰 발급 API는 1분에 1회만 호출 가능. 반드시 한 번 발급 후 캐싱하여 재사용해야 한다.

### 4.2 Hashkey 발급

POST 요청(주문 등)의 Body 데이터를 암호화하는 값. 주문 전에 반드시 발급받아야 한다.

```
POST {BASE_URL}/uapi/hashkey
Content-Type: application/json
appkey: {APP_KEY}
appsecret: {APP_SECRET}

{
  "CANO": "50012345",
  "ACNT_PRDT_CD": "01",
  "PDNO": "005930",
  ...주문 Body와 동일한 데이터...
}
```

**응답:**
```json
{
  "HASH": "abc123def456..."
}
```

**사용 방법:**
- 주문 API 호출 시 헤더에 `hashkey: {HASH값}` 추가
- Hashkey는 요청 Body가 바뀔 때마다 새로 발급해야 함
- GET 요청(시세 조회 등)에는 불필요

### 4.3 WebSocket 접속키 발급 (추후 Phase 5)

실시간 시세 수신 시에만 필요. 초기에는 사용하지 않음.

```
POST {BASE_URL}/oauth2/Approval
Content-Type: application/json

{
  "grant_type": "client_credentials",
  "appkey": "{APP_KEY}",
  "secretkey": "{APP_SECRET}"
}
```

---

## 5. API 호출 패턴

### 5.1 공통 요청 헤더

모든 REST API 호출 시 포함해야 하는 헤더:

```
authorization: Bearer {ACCESS_TOKEN}
appkey: {APP_KEY}
appsecret: {APP_SECRET}
tr_id: {해당 API의 TR ID}
Content-Type: application/json; charset=utf-8
custtype: P
```

### 5.2 GET 요청 (시세 조회) 패턴

```
GET {BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price
  ?FID_COND_MRKT_DIV_CODE=J
  &FID_INPUT_ISCD=005930

Headers:
  authorization: Bearer {ACCESS_TOKEN}
  appkey: {APP_KEY}
  appsecret: {APP_SECRET}
  tr_id: FHKST01010100
  custtype: P
```

- Query Parameter로 데이터 전달
- Hashkey 불필요
- `FID_COND_MRKT_DIV_CODE`: `J`(주식), `ETF`, `ETN` 등

### 5.3 POST 요청 (주문) 패턴

```
POST {BASE_URL}/uapi/domestic-stock/v1/trading/order-cash

Headers:
  authorization: Bearer {ACCESS_TOKEN}
  appkey: {APP_KEY}
  appsecret: {APP_SECRET}
  tr_id: TTTC0802U          ← 실전 매수 (모의: VTTC0802U)
  custtype: P
  hashkey: {HASH}            ← Hashkey 필수!

Body:
{
  "CANO": "50012345",
  "ACNT_PRDT_CD": "01",
  "PDNO": "005930",
  "ORD_DVSN": "01",
  "ORD_QTY": "10",
  "ORD_UNPR": "0"
}
```

- JSON Body로 데이터 전달
- **Hashkey 필수** (헤더에 포함)
- `ORD_DVSN`: `00`(지정가), `01`(시장가)
- 시장가 주문 시 `ORD_UNPR`은 `"0"`

### 5.4 TR ID 실전/모의 분기 규칙

| 구분 | 실전 접두어 | 모의 접두어 | 예시 |
|------|-----------|-----------|------|
| 주문 계열 | `TTTC` | `VTTC` | 매수: `TTTC0802U` → `VTTC0802U` |
| 시세 조회 | `FHKST` | `FHKST` (동일) | 현재가: `FHKST01010100` |

- 주문/계좌 API의 TR ID만 실전/모의가 다름
- 시세 조회 API의 TR ID는 동일
- 우리 프로젝트에서는 `KIS_IS_PAPER_TRADING` 설정값으로 자동 분기

---

## 6. 응답 구조 공통 패턴

### 6.1 정상 응답

```json
{
  "rt_cd": "0",
  "msg_cd": "MCA00000",
  "msg1": "정상처리 되었습니다.",
  "output": { ... }
}
```

| 필드 | 설명 |
|------|------|
| `rt_cd` | `"0"` = 성공, `"1"` = 실패 |
| `msg_cd` | 메시지 코드 |
| `msg1` | 응답 메시지 |
| `output` | 실제 데이터 (API마다 다름) |

### 6.2 에러 응답

```json
{
  "rt_cd": "1",
  "msg_cd": "EGW00123",
  "msg1": "기간이 만료된 token 입니다."
}
```

---

## 7. 주요 에러 코드 & 트러블슈팅

### 7.1 인증 관련

| 에러코드 | 메시지 | 원인 | 해결 |
|---------|--------|------|------|
| `EGW00123` | 기간이 만료된 token | 토큰 만료 | 토큰 재발급 |
| `EGW00133` | 접근토큰발급 제한 | 1분 내 토큰 중복 발급 | 토큰 캐싱 후 재사용. 1분 대기 후 재시도 |
| `EGW00201` | 유효하지 않은 appkey | 키 불일치 또는 환경 불일치 | 모의/실전 키 확인 |
| `EGW00202` | GW라우팅 오류 | Body 직렬화 문제 | JSON 문자열로 직렬화하여 전송 |

### 7.2 주문 관련

| 에러코드 | 메시지 | 원인 | 해결 |
|---------|--------|------|------|
| `APBK0919` | 매수가능금액 초과 | 잔액 부족 | 매수 전 잔고 확인 로직 추가 |
| `APBK0634` | 미체결주문 없음 | 취소할 주문 없음 | 주문 상태 확인 후 취소 요청 |

### 7.3 호출 제한 (Rate Limit)

| 상황 | 에러 | 해결 |
|------|------|------|
| 초당 호출 초과 | HTTP 429 | Rate Limiter 구현. 초당 최대 10건으로 보수적 설정 |
| 연속 429 발생 | 일시 차단 (1~10분) | 지수 백오프 (1초 → 2초 → 4초 → ...) 적용 |
| 심한 경우 | 계정 일시 정지 | API 호출 간격을 넉넉하게 설정 |

### 7.4 자주 하는 실수 체크리스트

- [ ] 모의투자 키로 실전투자 도메인 호출하고 있지 않은지?
- [ ] POST 요청에 Hashkey를 빠뜨리지 않았는지?
- [ ] TR ID를 모의/실전 환경에 맞게 설정했는지?
- [ ] 토큰을 매번 새로 발급하지 않고 캐싱하고 있는지?
- [ ] 계좌번호를 CANO(8자리)와 ACNT_PRDT_CD(2자리)로 분리했는지?
- [ ] `Content-Type`이 `application/json; charset=utf-8`인지?
- [ ] 주문 수량/가격을 **문자열**로 보내고 있는지? (숫자가 아님)

---

## 8. 프로젝트 적용 — 인증 흐름 설계

```
앱 시작
  │
  ▼
Settings (.env 로드)
  │ APP_KEY, APP_SECRET, BASE_URL, CANO, ACNT_PRDT_CD
  │
  ▼
KisAuthRepository.get_token()
  │
  ├─ 캐시에 유효한 토큰 있음?
  │     └─ YES → 캐시된 토큰 반환
  │
  └─ NO (최초 or 만료 임박)
        │
        ▼
      POST /oauth2/tokenP
        │
        ▼
      토큰 + 만료시간 메모리 캐싱
        │
        ▼
      토큰 반환

모든 API 호출 시:
  │
  ▼
KisAuthRepository.get_token() → 항상 유효한 토큰 보장
  │
  ▼
(POST 요청이면) KisAuthRepository.get_hashkey(body) → Hashkey 발급
  │
  ▼
API 호출 실행
  │
  ├─ 성공 (rt_cd == "0") → 정상 처리
  │
  └─ 실패
       ├─ 토큰 만료 → 토큰 재발급 후 재시도 (최대 1회)
       ├─ Rate Limit → 대기 후 재시도 (지수 백오프)
       └─ 기타 에러 → 로깅 + 예외 발생
```

---

## 9. httpx 비동기 클라이언트 사용 패턴

우리 프로젝트에서는 `requests` 대신 `httpx.AsyncClient`를 사용한다.

### 9.1 기본 사용법 (참고용 의사코드)

```python
# 앱 수명주기 동안 하나의 AsyncClient를 공유
client = httpx.AsyncClient(
    base_url=settings.kis_base_url,
    timeout=httpx.Timeout(10.0),
)

# GET 요청 (시세 조회)
response = await client.get(
    "/uapi/domestic-stock/v1/quotations/inquire-price",
    headers={...공통 헤더...},
    params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"},
)

# POST 요청 (주문)
response = await client.post(
    "/uapi/domestic-stock/v1/trading/order-cash",
    headers={...공통 헤더 + hashkey...},
    json={...주문 Body...},
)
```

### 9.2 AsyncClient 수명주기

- FastAPI의 `lifespan` 이벤트에서 생성/종료
- 앱 시작 시 `AsyncClient()` 생성
- 앱 종료 시 `await client.aclose()` 호출
- 연결 풀링으로 성능 최적화

### 9.3 타임아웃 설정

```python
timeout = httpx.Timeout(
    connect=5.0,    # 연결 타임아웃
    read=10.0,      # 읽기 타임아웃
    write=5.0,      # 쓰기 타임아웃
    pool=5.0,       # 연결 풀 타임아웃
)
```

---

## 10. Rate Limiting 전략

### 10.1 우리 프로젝트의 호출 제한 정책

| 항목 | 값 | 이유 |
|------|-----|------|
| 최대 초당 호출 | **10건** | 공식 20건의 50%. 안전 마진 확보 |
| 토큰 발급 간격 | **최소 60초** | 공식 1분 1회 제한 |
| 실패 시 재시도 | 최대 **3회** | 지수 백오프 적용 |
| 백오프 간격 | 1초 → 2초 → 4초 | 지수적 증가 |

### 10.2 스윙 트레이딩에서의 호출 빈도

규칙 기반 스윙 트레이딩은 초단타가 아니므로 호출 제한에 여유가 있다:

```
1회 스캔 (5분 간격):
  잔고 + 미체결 조회              = 2건
  보유 종목 현재가 (5종목)         = 5건
  관심 종목 현재가 (5종목)         = 5건
  관심 종목 일봉 (MA 계산, 0~5)   = 0~5건
  기타 (매수가능, 주문 등)         = 0~5건
  ──────────────────────────────────
  1회 스캔 합계: 약 12~28건
  초당 환산: 최대 28건 / 300초 = 0.09건/초 (여유 충분)
  일일 합계 (75회 스캔): 약 1,000~2,200건
```

> 호출 제한 상세 분석 (일일/월별/초당 제한 vs 우리 프로그램): [api-verification.md §10](./api-verification.md) 참조

---

## 11. 참고 리소스

| 리소스 | URL |
|--------|-----|
| KIS Developers 포털 | https://apiportal.koreainvestment.com/ |
| API 가이드 문서 | https://apiportal.koreainvestment.com/apiservice |
| 에러코드 조회 | https://apiportal.koreainvestment.com/faq-error-code |
| FAQ | https://apiportal.koreainvestment.com/faq |
| 공식 GitHub (샘플 코드) | https://github.com/koreainvestment/open-trading-api |
| 전체 API 문서 (Excel) | KIS Developers 포털 > API 문서 > 상단 다운로드 |
