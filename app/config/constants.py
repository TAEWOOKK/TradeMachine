from __future__ import annotations

# ── 이동평균선 (업계 표준) ──
MA_SHORT_PERIOD: int = 5
MA_LONG_PERIOD: int = 20
RSI_PERIOD: int = 14

# ── 매매 비용 (증권사·정부 제도) ──
# 수수료: 한국투자증권 온라인 0.015%
# 거래세: 2025년 코스피/코스닥 0.15% (농어촌특별세 포함)
BUY_FEE_RATE: float = 0.00015
SELL_FEE_RATE: float = 0.00015
SELL_TAX_RATE: float = 0.0015  # 0.15% (2025년 기준)
SLIPPAGE_RATE: float = 0.0015

# ── 종목 품질 필터 ──
MIN_STOCK_PRICE: int = 5_000
MIN_TRADING_VALUE: int = 1_000_000_000
MIN_TRADING_VOLUME: int = 50_000
MIN_MARKET_CAP: int = 500_000_000_000

# ── 안전장치 ──
ABNORMAL_CHANGE_RATE: float = 30.0
MAX_API_RETRY: int = 5
API_RETRY_DELAY_SECONDS: int = 2
MAX_CONSECUTIVE_FAILURES: int = 5
RATE_LIMIT_PER_SECOND: int = 2
TRADING_CUTOFF_MINUTES: int = 10

# ── 장 시간대 ──
MARKET_OPEN_HOUR: int = 9
MARKET_OPEN_MINUTE: int = 0
MARKET_CLOSE_HOUR: int = 15
MARKET_CLOSE_MINUTE: int = 30
SCAN_START_MINUTE: int = 5  # 09:05부터 스캔 시작
BUY_CUTOFF_MINUTE: int = 20  # 15:20 이후 매수 안 함
SCAN_END_MINUTE: int = 25  # 15:25 이후 스캔 안 함

# ── 시장 필터 임계값 ──
KOSPI_BUY_BLOCK_RATE: float = -1.0  # KOSPI 이만큼 빠지면 매수 차단
KOSPI_EMERGENCY_RATE: float = -1.5  # KOSPI 이만큼 빠지면 긴급 매도
CONSECUTIVE_STOP_LOSS_LIMIT: int = 2  # 연속 손절 이만큼이면 매수 중단

# ── 정산 ──
DEPOSIT_THRESHOLD: int = 1000  # 입출금 감지 최소 금액
CLEANUP_SCAN_LOG_DAYS: int = 90
CLEANUP_BOT_EVENT_DAYS: int = 30

# ── KRX 휴장일 (매년 갱신) ──
KRX_HOLIDAYS: set[str] = {
    "20260101", "20260216", "20260217", "20260218",
    "20260302", "20260501", "20260505", "20260525",
    "20260817", "20260924", "20260925", "20260928",
    "20261005", "20261009", "20261225", "20261231",
}
