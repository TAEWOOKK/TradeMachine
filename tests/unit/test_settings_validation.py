from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def _base_params() -> dict:
    return {
        "kis_app_key": "key",
        "kis_app_secret": "secret",
        "kis_cano": "12345678",
        "kis_acnt_prdt_cd": "01",
        "kis_base_url": "https://example.com",
        "watch_list": "005930",
    }


class TestSettingsValidation:
    def test_stop_loss_must_be_negative(self):
        """stop_loss_rate가 양수이면 ValidationError."""
        with pytest.raises(ValidationError, match="stop_loss_rate"):
            Settings(**{**_base_params(), "stop_loss_rate": 1.0})

    def test_take_profit_must_be_positive(self):
        """take_profit_rate가 음수이면 ValidationError."""
        with pytest.raises(ValidationError, match="take_profit_rate"):
            Settings(**{**_base_params(), "take_profit_rate": -1.0})

    def test_max_holding_count_not_negative(self):
        """max_holding_count가 -1이면 ValidationError."""
        with pytest.raises(ValidationError, match="max_holding_count"):
            Settings(**{**_base_params(), "max_holding_count": -1})
