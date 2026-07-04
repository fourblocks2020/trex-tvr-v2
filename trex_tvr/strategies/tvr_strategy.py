"""TvrStrategy — Target-Value Rebalancer constant-mix 리밸런싱 전략.

constant-mix 목표비율 리밸런싱:
  - 슬롯 row의 qty·cash_balance·current_price·strategy_params를 입력으로 받아
    아이템별 현재 주식비중을 계산하고 드리프트가 밴드를 초과하면 매수/매도/홀드 신호를 반환한다.
  - 전략 파라미터(target_weight, rebalance_band_pct, min_trade_krw)는 strategy_params에서 읽는다.
    실제 운용 시 trex-core 슬롯 row 경유로 portfolio-hub strategy_sets에서 전달받는다.
    단위 테스트는 fixture dict로 직접 주입한다.

시임 규칙:
  - 이 모듈은 transport, market_data, redis, pika, httpx, trex_core를 import하지 않는다.
  - evaluate()는 IO/clock/random이 없는 순수 결정론적 함수다.
"""
from __future__ import annotations

import math

from trex_tvr.strategies.base import BuyEvaluation, BuySignal, Diagnostic, HoldDecision, SellSignal


class TvrStrategy:
    """TVR constant-mix 리밸런싱 전략.

    strategy_params(target_weight, rebalance_band_pct, min_trade_krw)는 evaluate() 호출 시
    asset["strategy_params"]에서 읽는다. Constructor kwargs는 TOML [strategy.tvr_v1] 테이블에서
    오지만 TVR 파라미터는 runtime에 portfolio-hub에서 per-slot으로 전달받는다.
    """

    strategy_id = "tvr_v1"
    candle_count: int = 0  # TVR은 분봉 candles 불필요 — 현재가만 사용

    def __init__(self, **kwargs: object) -> None:
        # TOML [strategy.tvr_v1]의 키를 받지만 TVR은 strategy_params를 slot row에서 읽는다.
        # constructor kwargs는 무시해도 되지만 registry 호환을 위해 수용한다.
        pass

    def evaluate(self, asset: dict, snapshot: object, market_data: dict) -> BuyEvaluation:
        """리밸런싱 평가 — 순수 결정론 함수 (IO/clock/random 없음).

        입력:
          asset["qty"]               보유 수량 (int, 0 이상)
          asset["cash_balance"]      아이템 현금 잔고 (KRW int, 0 이상)
          asset["current_price"]     현재가 (KRW int/float, > 0이어야 유효)
          asset["strategy_params"]   {"target_weight": float, "rebalance_band_pct": float, "min_trade_krw": int}
          asset["symbol"]            종목코드
          asset["slot_id"]           슬롯 ID

        반환:
          BuySignal    드리프트 부족 & 매수 가능
          SellSignal   드리프트 초과 & 매도 가능
          HoldDecision 밴드 이내 또는 dust 거래
          Diagnostic   입력 신뢰 불가 (stale_quote, zero_allocation)
        """
        symbol = str(asset.get("symbol") or "")
        slot_id = str(asset.get("slot_id") or "")

        # --- stage 0: 현재가 유효성 검사 ---
        raw_price = asset.get("current_price")
        if raw_price is None:
            # market_data에서도 fallback 시도 (현재가 주입 경로 호환)
            raw_price = market_data.get("current_price")
        try:
            current_price = float(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            current_price = None
        if not current_price or current_price <= 0:
            return Diagnostic(symbol=symbol, reason="stale_quote", strategy_id=self.strategy_id)

        # --- stage 1: strategy_params 파싱 ---
        params = asset.get("strategy_params") or {}
        try:
            target_weight = float(params["target_weight"])
            rebalance_band_pct = float(params["rebalance_band_pct"])
            min_trade_krw = int(params["min_trade_krw"])
        except (KeyError, TypeError, ValueError):
            return Diagnostic(
                symbol=symbol,
                reason="stale_quote",  # strategy_params 누락은 daemon 레이어에서 Diagnostic(missing_strategy_params) 처리
                strategy_id=self.strategy_id,
            )

        # --- stage 2: 비중 계산 ---
        qty = int(asset.get("qty") or 0)
        cash_balance = int(asset.get("cash_balance") or 0)
        stock_value = qty * current_price
        total_allocation = stock_value + cash_balance

        if total_allocation <= 0:
            return Diagnostic(symbol=symbol, reason="zero_allocation", strategy_id=self.strategy_id)

        current_weight = stock_value / total_allocation
        drift = current_weight - target_weight
        band = rebalance_band_pct / 100.0

        # --- stage 3: 밴드 내이면 홀드 ---
        if abs(drift) <= band:
            return HoldDecision(symbol=symbol, reason="within_band", strategy_id=self.strategy_id)

        # --- stage 4: 매수 (부족) ---
        if drift < -band:
            target_stock_value = target_weight * total_allocation
            delta_krw = target_stock_value - stock_value
            buy_qty = math.floor(delta_krw / current_price)
            # cash 부족 시 축소
            max_buy_qty = math.floor(cash_balance / current_price)
            buy_qty = min(buy_qty, max_buy_qty)
            if buy_qty <= 0 or buy_qty * current_price < min_trade_krw:
                return HoldDecision(symbol=symbol, reason="dust_buy", strategy_id=self.strategy_id)
            return BuySignal(
                symbol=symbol,
                target_qty=buy_qty,
                allocated_capital=int(buy_qty * current_price),
                reason="rebalance_buy",
                strategy_id=self.strategy_id,
                entry_price=int(current_price),
                forced_close_at=None,
            )

        # --- stage 5: 매도 (초과) ---
        # drift > +band
        target_stock_value = target_weight * total_allocation
        delta_krw = stock_value - target_stock_value
        sell_qty = math.floor(delta_krw / current_price)
        # 과매도 방지: qty 상한 클램프
        sell_qty = min(sell_qty, qty)
        if sell_qty <= 0 or sell_qty * current_price < min_trade_krw:
            return HoldDecision(symbol=symbol, reason="dust_sell", strategy_id=self.strategy_id)
        return SellSignal(
            symbol=symbol,
            slot_id=slot_id,
            qty=sell_qty,
            reason="rebalance_sell",
            strategy_id=self.strategy_id,
        )
