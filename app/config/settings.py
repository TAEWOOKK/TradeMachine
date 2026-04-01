from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", populate_by_name=True)

    # ── Tier 1: .env 필수 (비밀키·환경) ──
    kis_app_key: str
    kis_app_secret: str
    kis_cano: str
    kis_acnt_prdt_cd: str
    kis_base_url: str
    watch_list: str

    # ── Tier 2: 기본값 있음 (.env 오버라이드 가능) ──
    kis_is_paper_trading: bool = True

    trading_interval_minutes: int = 5

    signal_lookback_days: int = 14
    signal_confirm_days: int = 3
    sell_confirm_days: int = 2

    volume_confirm_ratio: float = 1.5
    rsi_overbought: int = 70
    rsi_oversold: int = 30

    max_investment_ratio: float = 1.0  # (레거시) 1.0=미사용. 종목당 투자는 가용금/목표종목수로 계산
    max_holding_count: int = 0  # 0 = 제한 없음
    max_daily_buy_count: int = 3
    max_holding_days: int = 2  # 단타: 2영업일
    stop_loss_rate: float = -2.0  # 단타: -2%
    take_profit_rate: float = 2.0  # 단타: +2%
    trailing_stop_activate: float = 1.5  # 단타: +1.5%에서 트레일링 활성화
    trailing_stop_rate: float = 0.8  # 단타: 고점 대비 0.8% 하락 시 매도
    rebuy_cooldown_hours: int = 24

    # 단타 진입 필터
    scalping_entry_minute: int = 25  # 장 시작 후 N분 이후 매수 (0=제한없음)
    min_intraday_change: float = 0.3  # 전일대비 최소 상승률 (%)
    max_intraday_change: float = 4.0  # 전일대비 최대 상승률 (%)
    rsi_scalping_min: int = 47  # 단타 RSI 하한
    rsi_scalping_max: int = 65  # 단타 RSI 상한

    enable_market_filter: bool = True

    # 매 영업일 장 마감 직전(15:28) 보유 전량 청산 — 야간·익일 갭 리스크 회피
    # .env: EOD_CLOSE_ENABLED (구 FRIDAY_CLOSE_ENABLED 도 인식)
    eod_close_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("eod_close_enabled", "friday_close_enabled"),
    )

    @property
    def watch_list_codes(self) -> list[str]:
        return [c.strip() for c in self.watch_list.split(",") if c.strip()]

    @field_validator("max_investment_ratio")
    @classmethod
    def _validate_investment_ratio(cls, v: float) -> float:
        if not 0.0 < v <= 1.0:
            raise ValueError("max_investment_ratio는 0~1 사이여야 합니다")
        return v

    @field_validator("stop_loss_rate")
    @classmethod
    def _validate_stop_loss(cls, v: float) -> float:
        if v >= 0:
            raise ValueError("stop_loss_rate는 음수여야 합니다")
        return v

    @field_validator("take_profit_rate")
    @classmethod
    def _validate_take_profit(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("take_profit_rate는 양수여야 합니다")
        return v

    @field_validator("max_holding_count")
    @classmethod
    def _validate_holding_count(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_holding_count는 0 이상이어야 합니다 (0=제한없음)")
        return v

    @field_validator("max_daily_buy_count")
    @classmethod
    def _validate_daily_buy(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_daily_buy_count는 0 이상이어야 합니다 (0=무제한)")
        return v
