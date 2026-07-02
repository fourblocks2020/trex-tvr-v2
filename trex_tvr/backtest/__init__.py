"""trex_tvr.backtest — backtest infrastructure layer.

This package is infra-only:
  - NO import from strategies/ except strategies.base (BuyStrategy, BuySignal, BuyEvaluation)
  - NO import from trex_core
  - All live kis-data-hub calls happen in dump_job (separate step), never in the replay engine.
"""
