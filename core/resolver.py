"""What a strategy needs from the Instrument Master, as a Protocol.

Keeping this here means `core/strategy_base.py` never imports the broker
package, so strategies stay testable with a two-line fake resolver.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from core.contracts import Instrument, Segment


@runtime_checkable
class InstrumentResolver(Protocol):
    def resolve(
        self,
        underlying_key: str,
        segment: Segment,
        *,
        spot: float,
        bullish: bool = True,
        expiry_rule: str = "nearest_weekly",
        on_date: date | None = None,
    ) -> Instrument:
        """The instrument to trade: ATM CE/PE, current-month future, or the stock."""
        ...

    def strike_step(self, underlying_key: str, expiry_rule: str = "nearest_weekly") -> float: ...

    def lot_size(self, instrument_key: str) -> int: ...

    def freeze_quantity(self, instrument_key: str) -> int | None: ...
