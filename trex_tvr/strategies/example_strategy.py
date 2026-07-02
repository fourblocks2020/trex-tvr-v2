"""TvrStrategy — scaffold placeholder for Target-Value Rebalancer (TVR) buy logic.

This is the ONE place strategy logic lives. The runtime engine (daemon, transport,
market_data, intent builder) is strategy-agnostic and does not change per strategy.

TVR (Target-Value Rebalancer) concept:
  - Constant-mix 목표비율 리밸런싱 장기전략.
  - 슬롯별 보유 비율이 목표(target_weight)에서 threshold 이상 벗어나면 매수/매도로 복원.

TODO(설계): 리밸런싱 sell-intent 경로
  - 템플릿 프레임은 매수 전용(BuySignal)이며, 청산은 [exit] config 기반 trex-core 위임.
  - TVR은 리밸런싱을 위한 자체 부분매도(SellIntent.qty)가 필요하다.
  - 이후 구현에서 transport/intent_builder에 sell-intent(부분매도) 경로 추가 및
    decision-cycle을 리밸런싱형으로 확장하는 설계(Discovery/REQ)가 필요하다.
  - 현재 스캐폴드는 매수 진입(BuySignal)만 구현하며, 리밸런싱 매도는 미구현.

`evaluate()` returns exactly one of:
  - BuySignal    -> emit a buy intent for this asset
  - HoldDecision -> no buy this cycle
  - Diagnostic   -> input data can't be trusted (audited, skipped)
"""
from __future__ import annotations

from trex_tvr.strategies.base import BuyEvaluation, BuySignal, Diagnostic, HoldDecision


class TvrStrategy:
    """TVR scaffold: 현재가가 min_price 이상이면 매수 신호 발생(placeholder).

    실제 TVR 리밸런싱 로직은 목표비율(target_weight) 대비 현재 비율 편차를
    평가하는 방식으로 교체된다. 현재는 스캐폴드 통과용 최소 구현.
    Constructor kwargs come from the `[strategy.tvr_v1]` TOML table."""

    strategy_id = "tvr_v1"
    # candle_count = 400  # TVR은 candles 불필요할 수 있음 — 확정 시 조정

    def __init__(self, min_price: int = 0) -> None:
        # TODO(설계): TVR 파라미터 확정 시 target_weight, rebalance_threshold 등으로 교체
        self.min_price = int(min_price)

    def evaluate(self, asset: dict, snapshot: object, market_data: dict) -> BuyEvaluation:
        symbol = str(asset.get("symbol") or "")

        # Stage 0 — validate inputs (never trust market_data blindly).
        price = market_data.get("current_price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        if not price or price <= 0:
            return Diagnostic(symbol=symbol, reason="stale_quote", strategy_id=self.strategy_id)

        # --- PLACEHOLDER: TVR buy condition (교체 필요) -----------------------
        # 실제 TVR은 목표비율 편차 기반 리밸런싱 조건으로 교체한다.
        if price < self.min_price:
            return HoldDecision(symbol=symbol, reason="below_floor", strategy_id=self.strategy_id)
        # ----------------------------------------------------------------------

        # Position sizing: the runtime injects `entry_capital` (KRW per slot).
        entry_capital = int(asset.get("entry_capital", 0) or 0)
        target_qty = int(entry_capital // price)
        if target_qty <= 0:
            return HoldDecision(symbol=symbol, reason="insufficient_capital", strategy_id=self.strategy_id)

        return BuySignal(
            symbol=symbol,
            target_qty=target_qty,
            allocated_capital=entry_capital,
            reason="tvr_placeholder_buy",
            strategy_id=self.strategy_id,
            entry_price=int(price),
            forced_close_at=None,  # exit thresholds (SL/TP) come from [exit] config
        )
