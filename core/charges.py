"""Charge calculation from config/charges.yaml.

Rates are statutory and change: they are never hardcoded here. When the config
still has `null` rates the breakup is returned with `complete=False` so the UI
and the trade log can say "charges not configured" instead of showing a
plausible but invented number.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.contracts import Product, Segment, Side

ITEMS = ("brokerage", "stt", "exchange_txn", "sebi_fee", "stamp_duty", "gst")


@dataclass
class ChargeBreakup:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi_fee: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0
    complete: bool = True
    missing: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(getattr(self, item) for item in ITEMS), 2)

    def __add__(self, other: ChargeBreakup) -> ChargeBreakup:
        return ChargeBreakup(
            **{item: getattr(self, item) + getattr(other, item) for item in ITEMS},
            complete=self.complete and other.complete,
            missing=sorted(set(self.missing) | set(other.missing)),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total"] = self.total
        return data


class ChargesCalculator:
    """Per-leg, itemised charges. `rates` is the parsed config/charges.yaml."""

    def __init__(self, rates: dict[str, Any] | None = None) -> None:
        self.rates = rates or {}

    @property
    def effective_from(self) -> Any:
        return self.rates.get("effective_from")

    @property
    def configured(self) -> bool:
        return bool(self.effective_from) and self.rates.get("gst_rate") is not None

    def leg(
        self,
        *,
        segment: Segment,
        product: Product,
        side: Side,
        price: float,
        quantity: int,
    ) -> ChargeBreakup:
        turnover = price * quantity
        missing: list[str] = []

        def rate(path: str, default: float = 0.0) -> float:
            node: Any = self.rates
            for part in path.split("."):
                node = (node or {}).get(part) if isinstance(node, dict) else None
            if node is None:
                missing.append(path)
                return default
            return float(node)

        key = _rate_key(segment, product)
        # Brokerage may be a plain number or {flat_per_order, percent, max_per_order}.
        brokerage = _brokerage((self.rates.get("brokerage") or {}).get(key), turnover)
        if brokerage is None:
            missing.append(f"brokerage.{key}")
            brokerage = 0.0

        stt = 0.0
        if segment is Segment.OPTION and side is Side.SELL:
            stt = turnover * rate("stt.options_sell_premium")
        elif segment is Segment.FUTURE and side is Side.SELL:
            stt = turnover * rate("stt.futures_sell")
        elif segment is Segment.STOCK:
            if product is Product.DELIVERY:
                stt = turnover * rate("stt.equity_delivery")
            elif side is Side.SELL:
                stt = turnover * rate("stt.equity_intraday_sell")

        txn_key = {
            Segment.OPTION: "exchange_txn.nse_options_premium",
            Segment.FUTURE: "exchange_txn.nse_futures",
            Segment.STOCK: "exchange_txn.nse_equity",
        }[segment]
        exchange_txn = turnover * rate(txn_key)
        sebi_fee = turnover * rate("sebi_fee_per_crore") / 1e7
        stamp = turnover * rate(f"stamp_duty_buy.{key}") if side is Side.BUY else 0.0
        gst = (brokerage + exchange_txn + sebi_fee) * rate("gst_rate")

        return ChargeBreakup(
            brokerage=round(brokerage, 2),
            stt=round(stt, 2),
            exchange_txn=round(exchange_txn, 2),
            sebi_fee=round(sebi_fee, 4),
            stamp_duty=round(stamp, 2),
            gst=round(gst, 2),
            complete=not missing,
            missing=sorted(set(missing)),
        )

    def round_trip(
        self,
        *,
        segment: Segment,
        product: Product,
        entry_side: Side,
        entry_price: float,
        exit_price: float,
        quantity: int,
    ) -> ChargeBreakup:
        entry = self.leg(
            segment=segment, product=product, side=entry_side, price=entry_price, quantity=quantity
        )
        exit_leg = self.leg(
            segment=segment,
            product=product,
            side=entry_side.opposite,
            price=exit_price,
            quantity=quantity,
        )
        return entry + exit_leg


def _rate_key(segment: Segment, product: Product) -> str:
    if segment is Segment.OPTION:
        return "options"
    if segment is Segment.FUTURE:
        return "futures"
    return "equity_delivery" if product is Product.DELIVERY else "equity_intraday"


def _brokerage(cfg: Any, turnover: float) -> float | None:
    """Supports either a plain number, or {flat_per_order, percent, max_per_order}."""
    if cfg is None:
        return None
    if isinstance(cfg, int | float):
        return float(cfg)
    if isinstance(cfg, dict):
        flat = cfg.get("flat_per_order")
        percent = cfg.get("percent")
        value = float(flat) if flat is not None else 0.0
        if percent is not None:
            value = max(value, turnover * float(percent))
        cap = cfg.get("max_per_order")
        if cap is not None:
            value = min(value, float(cap))
        return value
    return None
