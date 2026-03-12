from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

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

    max_investment_ratio: float = 0.1
    max_holding_count: int = 5
    max_daily_buy_count: int = 3
    max_holding_days: int = 20
    stop_loss_rate: float = -5.0
    take_profit_rate: float = 15.0
    trailing_stop_activate: float = 8.0
    trailing_stop_rate: float = 4.0
    rebuy_cooldown_hours: int = 24

    enable_market_filter: bool = True

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
        if v < 1:
            raise ValueError("max_holding_count는 1 이상이어야 합니다")
        return v

    @field_validator("max_daily_buy_count")
    @classmethod
    def _validate_daily_buy(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_daily_buy_count는 1 이상이어야 합니다")
        return v
