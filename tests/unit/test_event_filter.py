"""대시보드 활동 로그 필터 로직 검증.

dashboard.html의 isTradeFilterMatch, isScanFilterMatch와 동일한 규칙을
Python으로 구현해 검증합니다. 매매 필터에서 분석(skip) 로그가 제외되는지,
분석 필터에서 조건 미달 로그가 포함되는지 확인합니다.
"""

from __future__ import annotations


def is_trade_filter_match(event: dict) -> bool:
    """매매 필터: 체결·실제 매매 검토만. 분석(skip) 제외."""
    if event.get("type") == "order_exec":
        return True
    if event.get("type") in ("buy_eval", "sell_eval"):
        data = event.get("data") or {}
        if data.get("action") and not data.get("skip"):
            return True
    return False


def is_scan_filter_match(event: dict) -> bool:
    """분석 필터: 스캔 시작/종료 + 조건 미달(skip) 이벤트."""
    if event.get("type") in ("scan_start", "scan_end"):
        return True
    if event.get("type") in ("buy_eval", "sell_eval"):
        data = event.get("data") or {}
        if data.get("skip"):
            return True
    return False


class TestTradeFilter:
    """매매 필터: 실제 매매 관련만 표시, 분석(skip) 제외."""

    def test_order_exec_included(self):
        e = {"type": "order_exec", "data": {"type": "BUY", "success": True}}
        assert is_trade_filter_match(e) is True

    def test_buy_eval_hit_included(self):
        e = {"type": "buy_eval", "data": {"action": "매수검토", "signal": "골든크로스"}}
        assert is_trade_filter_match(e) is True

    def test_sell_eval_hit_included(self):
        e = {"type": "sell_eval", "data": {"action": "매도검토", "reason": "TAKE_PROFIT"}}
        assert is_trade_filter_match(e) is True

    def test_buy_eval_skip_excluded(self):
        e = {"type": "buy_eval", "data": {"skip": "MA조건미충족", "ma5": 10000}}
        assert is_trade_filter_match(e) is False

    def test_buy_eval_rsi_skip_excluded(self):
        e = {"type": "buy_eval", "data": {"skip": "RSI과열", "rsi": 75}}
        assert is_trade_filter_match(e) is False

    def test_sell_eval_skip_excluded(self):
        e = {"type": "sell_eval", "data": {"skip": "보유유지"}}
        assert is_trade_filter_match(e) is False

    def test_scan_start_excluded(self):
        e = {"type": "scan_start", "data": {}}
        assert is_trade_filter_match(e) is False

    def test_pre_market_excluded(self):
        e = {"type": "pre_market", "data": {}}
        assert is_trade_filter_match(e) is False


class TestScanFilter:
    """분석 필터: 스캔·조건 미달 이벤트만 표시."""

    def test_scan_start_included(self):
        e = {"type": "scan_start", "data": {}}
        assert is_scan_filter_match(e) is True

    def test_scan_end_included(self):
        e = {"type": "scan_end", "data": {"elapsed_ms": 1200}}
        assert is_scan_filter_match(e) is True

    def test_buy_eval_ma_skip_included(self):
        e = {"type": "buy_eval", "data": {"skip": "MA조건미충족", "ma5": 10000}}
        assert is_scan_filter_match(e) is True

    def test_buy_eval_rsi_skip_included(self):
        e = {"type": "buy_eval", "data": {"skip": "RSI부족", "rsi": 40}}
        assert is_scan_filter_match(e) is True

    def test_buy_eval_hit_excluded(self):
        e = {"type": "buy_eval", "data": {"action": "매수검토", "signal": "골든크로스"}}
        assert is_scan_filter_match(e) is False

    def test_order_exec_excluded(self):
        e = {"type": "order_exec", "data": {"type": "BUY", "success": True}}
        assert is_scan_filter_match(e) is False

    def test_pre_market_excluded(self):
        e = {"type": "pre_market", "data": {}}
        assert is_scan_filter_match(e) is False


class TestFilterSeparation:
    """매매/분석 필터가 서로 겹치지 않도록 분리되는지 확인."""

    def test_skip_events_only_in_scan(self):
        skip_events = [
            {"type": "buy_eval", "data": {"skip": "MA조건미충족"}},
            {"type": "buy_eval", "data": {"skip": "RSI과열"}},
            {"type": "buy_eval", "data": {"skip": "잔액부족"}},
        ]
        for e in skip_events:
            assert is_trade_filter_match(e) is False
            assert is_scan_filter_match(e) is True

    def test_hit_events_only_in_trade(self):
        hit_events = [
            {"type": "buy_eval", "data": {"action": "매수검토", "signal": "골든크로스"}},
            {"type": "buy_eval", "data": {"action": "매수검토", "signal": "상승추세"}},
            {"type": "buy_eval", "data": {"action": "매수검토", "signal": "모멘텀"}},
        ]
        for e in hit_events:
            assert is_trade_filter_match(e) is True
            assert is_scan_filter_match(e) is False
