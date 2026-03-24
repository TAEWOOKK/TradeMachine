# 데이터 저장소 설계서

> TradeMachine에서 발생·수집·관리하는 모든 데이터의 저장 위치, 구조, 수명주기를 정의한 문서.
> **역할:** 데이터 저장소의 기준(SoT) 문서.
>
> 관련 문서:
> - 프로젝트 전체 계획 & .env 마스터: [project-plan.md](./project-plan.md)
> - 시스템 플로우 상세: [system-flow.md](./system-flow.md)

---

## 1. 저장소 전략 개요

```
┌──────────────────────────────────────────────────────────────┐
│                    TradeMachine 저장소 구조                     │
│                                                               │
│  ┌────────────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │  설정 (3계층)       │  │  SQLite DB   │  │  Python logging │ │
│  │  .env + settings   │  │  (영구 데이터) │  │  (파일 로그)     │ │
│  │  + constants       │  │              │  │                 │ │
│  └─────────┬──────────┘  └──────┬───────┘  └────────┬────────┘ │
│         │                │                      │             │
│         ▼                ▼                      ▼             │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                    메모리 (런타임)                       │   │
│  │  토큰, 보유종목, 미체결목록, 캐시(일봉/현재가)           │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### 저장소별 역할

| 저장소 | 역할 | 기술 | 수명 |
|--------|------|------|------|
| **설정 3계층** | .env(비밀키) + settings.py(전략) + constants.py(상수) | pydantic-settings | 영구 |
| **SQLite** | 매매 내역, 일일 리포트, 잔고 스냅샷 | aiosqlite | 영구 (앱 재시작 후에도 유지) |
| **로그 파일** | 스캔 로그, 에러 로그, API 모니터링 | Python logging (RotatingFileHandler) | 파일 보관 (30일 롤링) |
| **메모리** | 토큰, 보유종목, 미체결, API 캐시 | Python dict / dataclass | 앱 실행 중 (재시작 시 소멸) |

### 왜 SQLite인가?

| 고려사항 | SQLite | PostgreSQL |
|---------|--------|------------|
| 설치/설정 | **파일 하나, 설치 불필요** | 별도 서버 운영 필요 |
| 동시 접속 | 1 프로세스 (우리 앱 구조에 충분) | 다중 접속 지원 |
| 데이터 규모 | 하루 ~100건 매매 + 75건 스냅샷 | 대규모 데이터 처리 |
| 비동기 지원 | aiosqlite 라이브러리 사용 | asyncpg 사용 |
| 마이그레이션 | 추후 PostgreSQL로 전환 가능 | - |

> 우리 프로그램은 단일 프로세스, 하루 수백 건 수준이므로 **SQLite로 시작**한다.
> 추후 다중 전략 실행이나 웹 대시보드 등 확장 시 PostgreSQL로 마이그레이션한다.

---

## 2. 저장소 #1 — SQLite 데이터베이스

### DB 파일 위치

```
TradeMachine/
├── data/
│   └── trademachine.db      # SQLite DB 파일
```

> `.gitignore`에 `data/` 디렉토리를 추가하여 DB 파일이 버전 관리에 포함되지 않도록 한다.

---

### 2.1 테이블: `orders` (주문 내역)

> 매수/매도 주문이 실행될 때마다 1건씩 INSERT.

```sql
CREATE TABLE orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,    -- YYYY-MM-DD HH:MM:SS
    stock_code      TEXT    NOT NULL,    -- 종목코드 (005930)
    order_type      TEXT    NOT NULL,    -- BUY / SELL
    order_reason    TEXT    NOT NULL,    -- GOLDEN_CROSS / UPTREND_ENTRY / MOMENTUM_ENTRY / SCALPING_ENTRY / STOP_LOSS / TAKE_PROFIT / ...
    order_method    TEXT    NOT NULL DEFAULT 'MARKET',  -- MARKET(시장가) / LIMIT(지정가)
    quantity        INTEGER NOT NULL,    -- 주문 수량
    price           INTEGER NOT NULL,    -- 주문 가격 (시장가면 0)
    kis_order_no    TEXT,                -- KIS 주문번호 (ODNO)
    success         INTEGER NOT NULL DEFAULT 0,  -- 1=성공, 0=실패
    error_message   TEXT,                -- 실패 시 에러 메시지
    scan_time       TEXT                 -- 해당 스캔 시작 시각
);

CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_stock_code ON orders(stock_code);
```

| 필드 | 출처 |
|------|------|
| `order_reason` | system-flow.md §F2-3 [SELL], §F2-4 [STEP 10] — "매도 사유 기록" |
| `kis_order_no` | KIS API 응답 `ODNO` 필드 |
| `success` | KIS API 응답 `rt_cd` == "0" 여부 |

**예상 데이터량:** 하루 0~10건, 월 ~200건, 연 ~2,400건

#### 주요 쿼리 패턴

| 용도 | 쿼리 설명 |
|------|----------|
| 재매수 쿨다운 | 해당 종목의 가장 최근 SELL 성공 시각 (`ORDER BY created_at DESC LIMIT 1`) |
| 보유 기간 체크 (`get_first_buy_date`) | **마지막 매도 이후 첫 매수일**을 반환. 서브쿼리로 해당 종목의 마지막 SELL 성공 시각을 구한 뒤, 그 이후의 BUY 성공 기록 중 `ORDER BY created_at ASC LIMIT 1`로 첫 매수일을 조회한다. 가장 최근 매수일이 아님에 유의. |
| 일일 집계 | 오늘 날짜 LIKE로 매수/매도/실패 건수 집계 |

---

### 2.2 테이블: `daily_reports` (일일 리포트)

> 매일 15:30 장 마감 처리(F3) 시 1건 INSERT.

```sql
CREATE TABLE daily_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     TEXT    NOT NULL UNIQUE,  -- 영업일 (2026-03-11)
    buy_count       INTEGER NOT NULL DEFAULT 0,
    sell_count      INTEGER NOT NULL DEFAULT 0,
    unfilled_count  INTEGER NOT NULL DEFAULT 0,
    holding_count   INTEGER NOT NULL DEFAULT 0,  -- 보유 종목 수
    eval_amount     INTEGER NOT NULL DEFAULT 0,  -- 총 평가금액 (원)
    eval_profit     INTEGER NOT NULL DEFAULT 0,  -- 총 평가손익 (원)
    profit_rate     REAL    NOT NULL DEFAULT 0.0  -- 총 수익률 (%)
);
```

| 필드 | 출처 |
|------|------|
| 모든 필드 | system-flow.md §F3-3 "일일 매매 리포트 로그" |

**예상 데이터량:** 하루 1건, 연 ~250건

---

### 2.3 테이블: `balance_snapshots` (잔고 스냅샷)

> 매일 장 마감 시(F3) 보유 종목별 1건씩 INSERT.
> 일별 포트폴리오 변화를 추적하기 위한 테이블.

```sql
CREATE TABLE balance_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT    NOT NULL,    -- 영업일 (2026-03-11)
    stock_code      TEXT    NOT NULL,    -- 종목코드
    quantity        INTEGER NOT NULL,    -- 보유수량
    avg_price       REAL    NOT NULL,    -- 매입평균가 (소수점 가능)
    current_price   INTEGER NOT NULL,    -- 당일 종가 (현재가)
    eval_amount     INTEGER NOT NULL,    -- 평가금액 (현재가 × 수량)
    profit_rate     REAL    NOT NULL     -- 수익률 (%)
);

CREATE INDEX IF NOT EXISTS idx_balance_snapshot_date ON balance_snapshots(snapshot_date);
```

| 필드 | 출처 |
|------|------|
| `quantity`, `avg_price`, `profit_rate` | KIS API `inquire-balance` 응답 (system-flow.md §F2-1) |

**예상 데이터량:** 하루 0~5건, 월 ~100건, 연 ~1,200건

---

### 2.4 테이블: `scan_logs` (스캔 실행 로그)

> 매 스캔(F2) 완료 시 1건 INSERT. 시스템 동작 추적·디버깅용.

```sql
CREATE TABLE scan_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time       TEXT    NOT NULL,    -- 스캔 시작 시각
    holding_count   INTEGER NOT NULL,    -- 보유 종목 수
    sell_count      INTEGER NOT NULL DEFAULT 0,  -- 매도 실행 건수
    buy_count       INTEGER NOT NULL DEFAULT 0,  -- 매수 실행 건수
    skip_count      INTEGER NOT NULL DEFAULT 0,  -- SKIP 건수
    error_count     INTEGER NOT NULL DEFAULT 0,  -- 에러 건수
    api_call_count  INTEGER NOT NULL DEFAULT 0,  -- API 호출 횟수
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,  -- 소요 시간 (ms)
    note            TEXT                          -- 특이사항 메모
);

CREATE INDEX idx_scan_logs_scan_time ON scan_logs(scan_time);
```

| 필드 | 출처 |
|------|------|
| 모든 필드 | system-flow.md §F2-5 "스캔 결과 로깅" |

**예상 데이터량:** 하루 75건, 월 ~1,650건, 연 ~19,800건

> 데이터 정리: 90일 이상 지난 scan_logs는 주기적으로 삭제(또는 별도 아카이브)하여
> DB 파일 크기를 관리한다.

---

## 3. 저장소 #2 — 로그 파일

Python 표준 `logging` 모듈 + `RotatingFileHandler`를 사용한다.

### 로그 파일 구조

```
TradeMachine/
├── logs/
│   ├── app.log              # 앱 전반 로그 (INFO 이상)
│   ├── trading.log          # 매매 전용 로그 (매수/매도/SKIP 사유)
│   └── error.log            # 에러 전용 로그 (WARNING 이상)
```

### 로그 설정

| 항목 | 값 |
|------|-----|
| 파일 크기 제한 | 10MB per file |
| 백업 파일 수 | 5개 (app.log → app.log.1 ~ app.log.5) |
| 보관 용량 | 크기 기반 롤링 — 파일당 10MB × 6파일(원본+백업5) = 최대 60MB |
| 포맷 | `[2026-03-11 10:05:00] [INFO] [trading_service] 매수 실행: 005930 13주` |
| 인코딩 | UTF-8 |

### 로그 레벨별 용도

| 레벨 | 파일 | 내용 |
|------|------|------|
| **DEBUG** | app.log | API 요청/응답 상세, 캐시 히트/미스 |
| **INFO** | app.log, trading.log | 스캔 시작/종료, 매수/매도 실행, 잔고 동기화 |
| **WARNING** | app.log, error.log | API 재시도, 데이터 유효성 실패, 429 에러 |
| **ERROR** | app.log, error.log | API 연속 실패, 주문 실패, 토큰 발급 실패 |
| **CRITICAL** | app.log, error.log | 앱 시작 실패, 필수 설정 누락 |

### DB vs 로그 파일 — 역할 구분

| 데이터 | DB | 로그 파일 | 이유 |
|--------|----|---------|----|
| 주문 실행 결과 | O (orders) | O (trading.log) | DB=분석용 영구, 로그=실시간 추적 |
| 스캔 결과 요약 | O (scan_logs) | O (app.log) | DB=통계, 로그=디버깅 |
| SKIP 사유 상세 | X | O (trading.log) | 종목별 상세 사유는 로그만 |
| API 에러 상세 | X | O (error.log) | 에러 트레이스백은 로그만 |
| 일일 리포트 | O (daily_reports) | O (app.log) | DB=히스토리, 로그=당일 확인 |
| 잔고 스냅샷 | O (balance_snapshots) | X | DB에만 (로그는 불필요) |

---

## 4. 저장소 #3 — 메모리 (런타임)

앱 실행 중에만 유효한 데이터. 앱 재시작 시 `recover_state()`에서 DB + KIS API 잔고 조회로 자동 복구.

#### 재시작 시 복구 항목
| 데이터 | 복구 방법 |
|--------|----------|
| `_daily_buy_count` | DB `orders` 테이블에서 금일 성공 BUY 건수 조회 |
| `_highest_prices` | 현재 잔고의 현재가로 재초기화 |
| `_trailing_activated` | 현재 수익률이 `trailing_stop_activate` 이상이면 자동 복원 |
| Access Token | `run_pre_market()` 또는 첫 API 호출 시 재발급 |

### 4.1 메모리 데이터 목록

| 데이터 | 자료구조 | 갱신 주기 | 복구 방법 |
|--------|---------|----------|----------|
| Access Token | `str` + 만료시간 `datetime` | 만료 1시간 전 갱신 | F1에서 재발급 |
| 보유 종목 리스트 | `list[Position]` | 매 스캔 (F2-1) | inquire-balance API |
| 미체결 종목코드 | `set[str]` | 매 스캔 (F2-2) | inquire-daily-ccld API |
| API 호출 카운터 | `int` | 매 API 호출 | 0으로 초기화 (일일 리셋) |
| 연속 실패 카운트 | `int` | 성공 시 리셋 | 0으로 초기화 |

### 4.2 메모리 캐시

| 캐시 대상 | TTL | 키 | 값 | 절약 효과 |
|-----------|-----|------|------|----------|
| 현재가 시세 | **5초** | `price:{종목코드}` | API 응답 전체 | 같은 스캔 내 보유+관심 종목 겹침 시 |
| 일봉 데이터 | **5분** (1스캔) | `chart:{종목코드}:{days}` | 종가 배열 + MA 계산 결과 | 보유종목 매도판단 + 관심종목 매수판단에서 재사용 |

```
캐시 흐름 예시:

  F2-3 보유종목 매도 판단 중, 005930 현재가 조회
    → 캐시 MISS → API 호출 → 캐시에 저장 (TTL 5초)

  F2-4 관심종목 매수 판단 중, 005930 현재가 조회
    → 캐시 HIT → API 호출 안 함 (같은 스캔 내)
    → API 1건 절약

  같은 방식으로 일봉 데이터도 캐시 → 스캔당 최대 5건 절약 가능
```

### 4.3 캐시 구현 방향

- Python `dict` + `datetime` 기반 TTL 캐시 (외부 라이브러리 불필요)
- 또는 `cachetools.TTLCache` 사용 (pip install cachetools)
- Redis 등 외부 캐시는 이 규모에서는 과도함, 메모리 캐시로 충분

---

## 5. 저장소 #4 — 설정 (3계층)

> 설정 3계층 구조 상세: [project-plan.md §7 설정 관리](./project-plan.md) 참조.

| 저장 위치 | 설정 그룹 | 변경 빈도 | 민감도 |
|-----------|-----------|----------|--------|
| `.env` (Tier 1) | KIS API 인증, 계좌 정보, 관심 종목 | 거의 없음 | **높음** (.gitignore 필수) |
| `settings.py` (Tier 2) | 매매 전략 파라미터 (기본값 있음) | 가끔 (전략 튜닝 시) | 낮음 |
| `constants.py` (Tier 3) | 업계 표준, 비용, 품질 필터, 휴장일, 시스템 | 거의 없음 (코드 수정) | 낮음 |

---

## 6. 폴더 구조 반영

```
TradeMachine/
├── app/
│   ├── ...
│   ├── core/
│   │   ├── database.py          # SQLite 연결 관리 (aiosqlite)
│   │   └── cache.py             # 메모리 TTL 캐시
│   └── repository/
│       ├── ...
│       ├── order_log_repository.py   # orders 테이블 CRUD
│       └── report_repository.py      # daily_reports, balance_snapshots, scan_logs CRUD
├── data/
│   └── trademachine.db          # SQLite DB 파일 (자동 생성)
├── logs/
│   ├── app.log
│   ├── trading.log
│   └── error.log
├── .env
├── .env.example
└── .gitignore                   # data/, logs/, .env 포함
```

---

## 7. DB 초기화 & 마이그레이션

### 앱 시작 시 자동 테이블 생성

```
앱 시작 (F0)
  │
  ▼
[DB 초기화]
  │  data/ 디렉토리 존재 확인 → 없으면 생성
  │  trademachine.db 존재 확인 → 없으면 자동 생성
  │  각 테이블 존재 확인 → 없으면 CREATE TABLE 실행
  │  (IF NOT EXISTS 사용으로 기존 데이터 보존)
  │
  ▼
정상 초기화 완료
```

### 추후 마이그레이션 (Phase 7)

- SQLite → PostgreSQL 전환 시: SQLAlchemy ORM 또는 Alembic 마이그레이션 도구 사용
- 테이블 스키마는 동일하게 유지, 연결 문자열만 변경

---

## 8. 데이터 정리 정책

| 테이블 | 보관 기간 | 정리 방법 |
|--------|----------|----------|
| `orders` | **영구** | 삭제하지 않음 (연 ~2,400건, 무시할 수준) |
| `daily_reports` | **영구** | 삭제하지 않음 (연 ~250건) |
| `balance_snapshots` | **1년** | 1년 이상 지난 데이터 DELETE (추후) |
| `scan_logs` | **90일** | 90일 이상 지난 데이터 DELETE (주기적) |
| 로그 파일 | **자동 롤링** | RotatingFileHandler가 10MB × 5파일로 자동 관리 |

---

## 9. 구현 Phase 매핑

| Phase | 저장소 작업 |
|-------|-----------|
| **Phase 1** | `.env` + pydantic-settings, 로그 파일 설정 (logging config) |
| **Phase 2** | SQLite 연결 (`database.py`), `orders` 테이블, 메모리 캐시 (`cache.py`) |
| **Phase 3** | `scan_logs` 테이블, `trading.log` 매매 전용 로그 |
| **Phase 4** | `daily_reports` + `balance_snapshots` 테이블, 데이터 정리 배치 |
| **Phase 7** | PostgreSQL 마이그레이션 (선택), 웹 대시보드용 조회 API |

---

## 10. 의존성 추가

```
# pyproject.toml [project] dependencies에 포함
aiosqlite              # SQLite 비동기 드라이버
```

> `cachetools`는 선택 사항. Python dict + datetime으로 직접 구현 가능.
> PostgreSQL 전환 시: `asyncpg`, `sqlalchemy[asyncio]` 추가.
