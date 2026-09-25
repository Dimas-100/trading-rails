"""Template for a new broker adapter. Copy this file to adapters/<name>.py, fill in the methods, then add
the name to adapters/__init__.py (`available()` and `get_broker()`).

Contract (see broker.py):
  Broker    — name, armed(), account_id(), preview(order)->dict, place(order)->PlaceResult,
              cancel(client_order_id)->dict, open_orders()->list[dict with OPEN_ORDER_KEYS],
              positions()->list[Position], balance()->Balance
  BarSource — bars(symbol, n, as_of=None)->list[Bar] ASCENDING (use data.sort_bars),
              last_price(symbol, as_of=None)->float
Rules that keep the rails intact:
  * place() MUST return PlaceResult(placed=<did the broker accept it>, order_id, raw). The runner branches
    only on .placed; returning the raw body once made a runner abandon accepted orders.
  * armed() MUST read an explicit opt-in (the Webull adapter uses RAILS_LIVE_ENABLED=1). Default False.
  * Raise BrokerError for any failure; the runner logs it and continues with the next symbol.
  * Translate from the neutral Order in ONE pure function (like webull.to_webull) so it can be unit-tested
    without the network.
"""
from __future__ import annotations

from ..broker import BrokerError
from ..models import Balance, Bar, Order, PlaceResult, Position


class TemplateBroker:
    name = "template"

    def __init__(self, client, *, environ=None):
        self._client = client
        self._environ = environ or {}

    def armed(self) -> bool:
        return str(self._environ.get("RAILS_LIVE_ENABLED", "")).strip() == "1"

    def account_id(self) -> str:
        raise BrokerError("implement account_id()")

    def preview(self, order: Order) -> dict:
        raise BrokerError("implement preview()")

    def place(self, order: Order) -> PlaceResult:
        raise BrokerError("implement place() — and return a PlaceResult")

    def cancel(self, client_order_id: str) -> dict:
        raise BrokerError("implement cancel()")

    def open_orders(self) -> list[dict]:
        """Every working order as a dict with OPEN_ORDER_KEYS. Raise on a payload you do not recognise:
        unknown must never read as flat (an empty list would let the runner place a second exit/entry)."""
        raise BrokerError("implement open_orders()")

    def positions(self) -> list[Position]:
        """Every held position. Raise on a payload you do not recognise: unknown must never read as flat."""
        raise BrokerError("implement positions()")

    def balance(self) -> Balance:
        """Cash, buying power, net liquidation value. Unknown must never read as flat (zero)."""
        raise BrokerError("implement balance()")

    def bars(self, symbol: str, n: int, as_of: str | None = None) -> list[Bar]:
        return []

    def last_price(self, symbol: str, as_of: str | None = None) -> float:
        raise BrokerError("implement last_price()")
