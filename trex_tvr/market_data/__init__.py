"""Market-data layer: kis-data-hub HTTP adapter (+ holiday cache).

trex-core snapshot responses carry no price, so the strategy's market_data
(current_price / candles) is always sourced here. Keeping IO in this layer is
what lets the strategy stay pure (the seam).
"""

from trex_tvr.market_data.kis_data_hub_client import (
    KisDataHubClient,
    KisDataHubClientError,
)

__all__ = ["KisDataHubClient", "KisDataHubClientError"]
