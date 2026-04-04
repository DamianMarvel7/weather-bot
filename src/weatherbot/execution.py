"""
Execution layer: paper vs live trading interface.

Both executors expose the same two methods:
  enter(token_id, ask, bid) -> FillResult   # open a YES position
  exit(token_id, bid)       -> FillResult   # close an existing position

PaperExecutor:  validates live CLOB prices, records fills at ask/bid — no orders placed.
LiveExecutor:   NotImplementedError stub — implement CLOB order placement to go live.

To switch from paper to live:
  1. Implement LiveExecutor.enter() and LiveExecutor.exit() using py-clob-client or
     direct POST to https://clob.polymarket.com/order.
  2. Add CLOB_API_KEY and WALLET_PRIVATE_KEY to .env.
  3. In WeatherBot.__init__, pass executor=LiveExecutor().
"""

from dataclasses import dataclass


@dataclass
class FillResult:
    filled:     bool
    fill_price: float
    reason:     str   # "ok" | "no_ask" | "no_bid" | "error"


class PaperExecutor:
    """
    Simulates execution using live CLOB prices.

    Entry fills at ask; exit fills at bid.
    No orders are placed — prices come from the CLOB orderbook fetched
    immediately before the call in _maybe_open / monitor_stops.

    Slippage is already enforced upstream via MAX_SLIPPAGE before the
    executor is called, so no additional modelling is needed here.
    """

    def enter(self, token_id: str,
              ask: float | None,
              bid: float | None) -> FillResult:
        if ask is None:
            return FillResult(False, 0.0, "no_ask")
        return FillResult(True, ask, "ok")

    def exit(self, token_id: str,
             bid: float | None) -> FillResult:
        if bid is None:
            return FillResult(False, 0.0, "no_bid")
        return FillResult(True, bid, "ok")


class LiveExecutor:
    """
    Live trading executor — places real limit orders on Polymarket CLOB.

    NOT YET IMPLEMENTED. Raises NotImplementedError on any call.

    To implement:
      1. Install py-clob-client or use requests to POST to /order.
      2. Load CLOB_API_KEY and WALLET_PRIVATE_KEY from .env via config._ENV.
      3. Sign the order payload (EIP-712) and submit.
      4. Return FillResult with the confirmed fill price from the order response.

    Requirements once implemented:
      - CLOB_API_KEY=<your-key>  in .env
      - WALLET_PRIVATE_KEY=<0x...> in .env
      - USDC approval granted to the Polymarket Exchange contract on Polygon
    """

    def enter(self, token_id: str,
              ask: float | None,
              bid: float | None) -> FillResult:
        raise NotImplementedError(
            "LiveExecutor.enter() not implemented. "
            "See execution.py docstring for implementation steps."
        )

    def exit(self, token_id: str,
             bid: float | None) -> FillResult:
        raise NotImplementedError(
            "LiveExecutor.exit() not implemented. "
            "See execution.py docstring for implementation steps."
        )
