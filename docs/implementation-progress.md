# TradeMachine 구현 진행 계획서

> 이 문서는 코드 구현의 **체크리스트이자 진행 상황 추적 문서**다.
> 각 항목을 구현할 때마다 `[ ]` → `[x]`로 체크하고, 날짜를 기록한다.
>
> - 설계 상세: [implementation-spec.md](./implementation-spec.md)
> - 전략 규칙: [trading-strategy.md](./trading-strategy.md)
> - 시스템 플로우: [system-flow.md](./system-flow.md)
> - 설정 3계층: [project-plan.md §7](./project-plan.md)

---

## Phase 1: 기반 구조 (뼈대)

> 목표: FastAPI 서버가 기동되고, 설정/로그/DB가 동작하는 상태

### 1-1. 프로젝트 스캐폴딩

| # | 작업 | 파일 | 상태 | 완료일 |
|---|------|------|------|--------|
| 1 | 폴더 구조 생성 + `__init__.py` | `app/`, `app/config/`, `app/core/`, `app/model/`, `app/repository/`, `app/service/`, `app/router/`, `app/scheduler/`, `data/`, `logs/` | [ ] | |
| 2 | `requirements.txt` 작성 | `requirements.txt` | [ ] | |
| 3 | `.env.example` 템플릿 | `.env.example` | [ ] | |
| 4 | `.gitignore` | `.gitignore` | [ ] | |

### 1-2. Config 계층

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 5 | Settings 클래스 (Tier 1 + Tier 2) | `app/config/settings.py` | spec §1.1 | [ ] | |
| 6 | 코드 상수 (Tier 3) | `app/config/constants.py` | spec §1.2 | [ ] | |

### 1-3. Core 계층 (기반)

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 7 | 로그 설정 (RotatingFileHandler 3개) | `app/core/logging_config.py` | spec §9 | [ ] | |
| 8 | 커스텀 예외 클래스 | `app/core/exceptions.py` | spec §2.3 | [ ] | |
| 9 | Rate Limiter | `app/core/rate_limiter.py` | spec §2.4 | [ ] | |

### 1-4. Model 계층

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 10 | 도메인 모델 (DailyCandle, MaResult, StockPrice, Position, ScanResult, OrderReason 등) | `app/model/domain.py` | spec §3.1 | [ ] | |
| 11 | 요청/응답 DTO (`OrderRequest`, `OrderResponse`, `BalanceResponse`, `StockPriceResponse`) | `app/model/dto.py` | spec §3.2 | [ ] | |

### 1-5. 앱 진입점

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 12 | FastAPI 앱 팩토리 (빈 lifespan) | `app/main.py` | spec §8 | [ ] | |
| 13 | uvicorn 엔트리포인트 | `main.py` (루트) | spec §8 | [ ] | |

### 1-✅ 검증

| # | 검증 항목 | 상태 | 완료일 |
|---|----------|------|--------|
| V1 | `python main.py` → FastAPI 서버 기동 확인 | [ ] | |
| V1.5 | `.env` 필수값 누락 시 앱 시작 실패 + 에러 메시지 확인 | [ ] | |
| V2 | `http://localhost:8000/docs` 접속 가능 | [ ] | |
| V3 | `logs/` 디렉토리에 로그 파일 생성 확인 | [ ] | |

---

## Phase 2: KIS API 연동 + 인프라

> 목표: KIS API로 토큰 발급, 현재가 조회, 주문 실행이 되는 상태

### 2-1. Core 인프라

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 14 | SQLite 연결 + 테이블 자동 생성 (4개 테이블) | `app/core/database.py` | spec §2.1 | [ ] | |
| 15 | TTL 캐시 (현재가 5초, 일봉 5분) | `app/core/cache.py` | spec §2.2 | [ ] | |

### 2-2. Repository 계층 — KIS API

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 16 | 인증 토큰 발급/캐싱/갱신 + hashkey | `app/repository/kis_auth_repository.py` | spec §4.1 | [ ] | |
| 17 | 현재가 조회 (`get_current_price`) + 공통 요청 래퍼 (`_request_get`) | `app/repository/market_data_repository.py` | spec §4.2 | [ ] | |
| 18 | 일봉 조회 (`get_daily_chart`) | ↑ 동일 파일 | spec §4.2 | [ ] | |
| 19 | 잔고 조회 (`get_balance`) | `app/repository/order_repository.py` | spec §4.3 | [ ] | |
| 20 | 미체결 조회 (`get_unfilled_orders`) | ↑ 동일 파일 | spec §4.3 | [ ] | |
| 21 | 매수/매도 주문 실행 (`execute_order`) | ↑ 동일 파일 | spec §4.3 | [ ] | |
| 22 | 주문 취소 (`cancel_order`) | ↑ 동일 파일 | spec §4.3 | [ ] | |
| 23 | 매수가능금액 조회 (`get_available_cash`) | ↑ 동일 파일 | spec §4.3 | [ ] | |

### 2-3. Repository 계층 — DB

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 24 | 주문 기록 INSERT (`save_order`) | `app/repository/order_log_repository.py` | spec §4.4 | [ ] | |
| 25 | 마지막 매도 시각 조회 (`get_last_sell_time`) | ↑ 동일 파일 | spec §4.4 | [ ] | |
| 26 | 최초 매수일 조회 (`get_first_buy_date`) | ↑ 동일 파일 | spec §4.4 | [ ] | |
| 27 | 오늘 매매 건수 조회 (`get_today_counts`) | ↑ 동일 파일 | spec §4.4 | [ ] | |

### 2-4. Stub 생성 (DI 의존성 해결)

> Phase 3에서 구현할 클래스의 껍데기를 먼저 만들어 DI 컨테이너가 동작하도록 한다.

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 28 | `ReportRepository` 클래스 stub (`__init__`만, 메서드는 Phase 3~4) | `app/repository/report_repository.py` | spec §4.5 | [ ] | |
| 29 | `TradingService` 클래스 stub (`__init__`만, 메서드는 Phase 3) | `app/service/trading_service.py` | spec §5 | [ ] | |

### 2-5. DI 컨테이너

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 30 | DI 초기화/종료 + getter (`init_dependencies`, `close_dependencies`, `get_settings`, `get_trading_service`, `get_market_repo`, `get_order_repo`) | `app/core/dependencies.py` | spec §2.5 | [ ] | |
| 31 | lifespan에 DI 연결 | `app/main.py` 수정 | spec §8 | [ ] | |

### 2-6. Router (시세 조회)

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 32 | `GET /market/price/{stock_code}` | `app/router/market_router.py` | spec §7.1 | [ ] | |
| 33 | `GET /market/balance` | ↑ 동일 파일 | spec §7.1 | [ ] | |

### 2-✅ 검증

| # | 검증 항목 | 상태 | 완료일 |
|---|----------|------|--------|
| V4 | 서버 기동 → `/market/price/005930` 호출 → 삼성전자 현재가 반환 | [ ] | |
| V5 | `/market/balance` 호출 → 계좌 잔고 반환 | [ ] | |
| V6 | `data/trademachine.db` 파일 생성 + 4개 테이블 존재 | [ ] | |
| V7 | 토큰 만료 후 자동 갱신 동작 확인 | [ ] | |

---

## Phase 3: 매매 전략 + 보조지표

> 목표: 수동 스캔으로 매수/매도 판단이 동작하는 상태

### 3-1. 전략 계산 메서드

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 34 | 이동평균선 계산 (`_calculate_ma`) | `app/service/trading_service.py` | spec §5.4 | [ ] | |
| 35 | RSI 계산 (`_calculate_rsi`) | ↑ | spec §5.8 | [ ] | |
| 36 | 골든크로스 판별 (`_check_golden_cross`) | ↑ | spec §5.5 | [ ] | |
| 37 | 교차일 인덱스 (`_find_cross_day_index`) | ↑ | spec §5.7 | [ ] | |
| 38 | 거래량 확인 (`_check_volume_confirmation`) | ↑ | spec §5.9 | [ ] | |
| 39 | 데드크로스 판별 (`_check_dead_cross`) | ↑ | spec §5.6 | [ ] | |

### 3-2. 매도 로직

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 40 | 트레일링 상태 정리 (`_cleanup_trailing`) | ↑ | spec §5.10 | [ ] | |
| 41 | 영업일 계산 (`_count_business_days`) | ↑ | spec §5.11 | [ ] | |
| 42 | 매도 실행 헬퍼 (`_execute_sell`) | ↑ | spec §5.12 | [ ] | |
| 43 | 매도 판단 로직 (`_evaluate_sell`) — 5단계 우선순위 | ↑ | spec §5.2 | [ ] | |

### 3-3. 매수 로직

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 44 | 매수 판단 로직 (`_evaluate_buy`) — 시장필터, 품질필터, 골든크로스, 거래량, RSI | ↑ | spec §5.3 | [ ] | |

### 3-4. 스캔 + 미체결 정리

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 45 | 미체결 정리 (`_cleanup_unfilled_orders`) | ↑ | spec §5.15 | [ ] | |
| 46 | 스캔 메인 (`run_scan`) | ↑ | spec §5.1 | [ ] | |

### 3-5. 리포트 저장

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 47 | scan_logs INSERT (`save_scan_log`) | `app/repository/report_repository.py` | spec §4.5 | [ ] | |

### 3-6. Router (매매)

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 48 | `POST /trading/scan` (수동 스캔) | `app/router/trading_router.py` | spec §7.2 | [ ] | |
| 49 | `POST /trading/order` (수동 매수/매도 주문) | ↑ | spec §7.2 | [ ] | |

### 3-✅ 검증

| # | 검증 항목 | 상태 | 완료일 |
|---|----------|------|--------|
| V8 | `POST /trading/scan` → 관심종목 스캔 → 매수/매도/SKIP 판단 로그 | [ ] | |
| V9 | `trading.log`에 매매 판단 사유 기록 확인 | [ ] | |
| V10 | `scan_logs` 테이블에 스캔 결과 저장 확인 | [ ] | |
| V11 | 모의투자 환경에서 실제 매수 주문 체결 확인 | [ ] | |
| V11.5 | 보유 종목에 대해 손절/데드크로스 매도 판단 동작 확인 | [ ] | |

---

## Phase 4: 스케줄러 & 자동화

> 목표: 봇이 장 시간에 자동으로 매매하는 상태

### 4-1. 장 시작/마감 처리

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 50 | 장 시작 전 준비 (`run_pre_market`) | `app/service/trading_service.py` | spec §5.13 | [ ] | |
| 51 | 장 마감 처리 (`run_post_market`) | ↑ | spec §5.14 | [ ] | |

### 4-2. 리포트

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 52 | daily_reports INSERT (`save_daily_report`) | `app/repository/report_repository.py` | spec §4.5 | [ ] | |
| 53 | balance_snapshots INSERT (`save_balance_snapshot`) | ↑ | spec §4.5 | [ ] | |
| 54 | scan_logs 90일 초과 삭제 (`cleanup_old_scan_logs`) | ↑ | spec §4.5 | [ ] | |

### 4-3. 스케줄러

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 55 | APScheduler 3 Jobs 등록 (pre_market, scan, post_market) | `app/scheduler/trading_scheduler.py` | spec §6 | [ ] | |
| 56 | lifespan에 스케줄러 start/shutdown 연결 | `app/main.py` 수정 | spec §8 | [ ] | |

### 4-4. Router (수동 제어)

| # | 작업 | 파일 | 명세 참조 | 상태 | 완료일 |
|---|------|------|----------|------|--------|
| 57 | `POST /trading/pre-market` (수동 장전 준비) | `app/router/trading_router.py` | spec §7.2 | [ ] | |
| 58 | `POST /trading/post-market` (수동 장마감) | ↑ | spec §7.2 | [ ] | |

### 4-✅ 검증

| # | 검증 항목 | 상태 | 완료일 |
|---|----------|------|--------|
| V12 | 08:50 → `run_pre_market` 자동 실행 확인 (토큰 발급, 관심종목 로드) | [ ] | |
| V13 | 09:05~15:25 → 5분 간격 `run_scan` 자동 실행 확인 | [ ] | |
| V14 | 15:30 → `run_post_market` 자동 실행 확인 (일일 리포트) | [ ] | |
| V15 | 주말/휴장일 → Job SKIP 확인 | [ ] | |
| V16 | `daily_reports` 테이블에 일일 리포트 저장 확인 | [ ] | |
| V16.5 | `balance_snapshots` 테이블에 종목별 스냅샷 저장 확인 | [ ] | |
| V17 | 모의투자 환경에서 하루 종일 자동 동작 (에러 없이 장 마감까지) | [ ] | |

---

## 구현 통계

| 지표 | 수치 |
|------|------|
| **총 구현 항목** | 58개 |
| **총 검증 항목** | 20개 |
| **완료된 구현** | 0 / 58 |
| **완료된 검증** | 0 / 20 |
| **현재 Phase** | - |
| **마지막 작업일** | - |

---

## 파일 → 항목 매핑 (역참조)

> 어떤 파일을 작업할 때 관련 항목을 빠르게 찾기 위한 인덱스.

| 파일 | 항목 번호 |
|------|----------|
| `app/config/settings.py` | #5 |
| `app/config/constants.py` | #6 |
| `app/core/logging_config.py` | #7 |
| `app/core/exceptions.py` | #8 |
| `app/core/rate_limiter.py` | #9 |
| `app/core/database.py` | #14 |
| `app/core/cache.py` | #15 |
| `app/core/dependencies.py` | #30 |
| `app/model/domain.py` | #10 |
| `app/model/dto.py` | #11 |
| `app/repository/kis_auth_repository.py` | #16 |
| `app/repository/market_data_repository.py` | #17, #18 |
| `app/repository/order_repository.py` | #19~#23 |
| `app/repository/order_log_repository.py` | #24~#27 |
| `app/repository/report_repository.py` | #28 (stub), #47, #52~#54 |
| `app/service/trading_service.py` | #29 (stub), #34~#46, #50, #51 |
| `app/router/market_router.py` | #32, #33 |
| `app/router/trading_router.py` | #48, #49, #57, #58 |
| `app/scheduler/trading_scheduler.py` | #55 |
| `app/main.py` | #12, #31, #56 |
| `main.py` (루트) | #13 |
| `requirements.txt` | #2 |
| `.env.example` | #3 |
| `.gitignore` | #4 |

---

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-03-11 | 계획서 초안 작성. Phase 1~4 총 55개 구현 + 17개 검증 항목 정의. |
| 2026-03-11 | 교차검증 반영: Phase 의존성 해결(stub 항목 추가), /trading/order 엔드포인트 추가, 검증 3개 추가, 명칭 통일. 58개 구현 + 20개 검증으로 확장. |
