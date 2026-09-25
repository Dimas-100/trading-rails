"""Behavioural gate tests: a spy broker records every call. Nothing reaches place/cancel unless
confirm is the literal True AND the broker is armed AND (if given) ask() said yes."""
from trading_rails.config import RunConfig
from trading_rails.models import Balance, Bar, Order, OrderType, PlaceResult, Position, Side, Signal
from trading_rails.runner import CycleReport, execute, gate_decision


class Spy:
    name = "spy"

    def __init__(self, armed=True, positions=(), open_orders=(), raise_in_preview=False):
        self._armed, self._positions, self._open = armed, list(positions), list(open_orders)
        self.calls = []
        self.raise_in_preview = raise_in_preview

    def armed(self): return self._armed
    def account_id(self): return "spy"
    def preview(self, order):
        if self.raise_in_preview and order.symbol == "BAD":
            raise RuntimeError("boom")
        self.calls.append(("preview", order))
        return {"ok": True}
    def place(self, order):
        self.calls.append(("place", order))
        return PlaceResult(placed=True, order_id="1")
    def cancel(self, cid):
        self.calls.append(("cancel", cid))
        return {"cancelled": True}
    def open_orders(self): return self._open
    def positions(self): return self._positions
    def balance(self): return Balance(10_000.0, 10_000.0, 10_000.0)

    def placed(self): return [c for c in self.calls if c[0] == "place"]
    def cancelled(self): return [c for c in self.calls if c[0] == "cancel"]


class Const:
    def __init__(self, signal, price=100.0):
        self.signal, self.price = signal, price
    name = "const"
    def warmup(self): return 1
    def on_bars(self, bars): return self.signal


class Bars:
    def __init__(self, price=100.0): self.price = price
    def bars(self, symbol, n, as_of=None):
        return [Bar("2024-01-0%d" % (i + 1), self.price, self.price, self.price, self.price) for i in range(3)]
    def last_price(self, symbol, as_of=None): return self.price


CFG = RunConfig(symbols=("SPY",), dollars_per_position=1000.0, max_positions=1, max_order_notional=5000.0)


def run(broker, signal, **kw):
    return execute(CFG, Const(signal), broker, Bars(), log_path=None, **kw)


def test_gate_decision_table():
    assert gate_decision(Spy(armed=True), True) == "submit"
    assert gate_decision(Spy(armed=True), False) == "dry-run"
    assert gate_decision(Spy(armed=False), True) == "refused"
    assert gate_decision(Spy(armed=True), "yes") == "dry-run"


def test_dry_run_default_never_places_or_cancels():
    b = Spy(positions=[Position("SPY", 10, 90.0, 100.0)],
            open_orders=[{"client_order_id": "s1", "symbol": "SPY", "side": "SELL", "order_type": "STOP",
                          "quantity": 10, "limit_price": None, "stop_price": 80.0, "status": "open"}])
    rep = run(b, Signal(Side.SELL, None, "out"))
    assert isinstance(rep, CycleReport)
    assert b.placed() == [] and b.cancelled() == []
    assert rep.dry_run == 2 and rep.placed == 0            # the cancel and the SELL both dry-run
    assert [c[0] for c in b.calls] == ["preview"]           # previews still happen


def test_unarmed_broker_refuses_even_with_confirm_true():
    b = Spy(armed=False)
    rep = run(b, Signal(Side.BUY, 90.0, "go"), confirm=True)
    assert b.placed() == [] and rep.refused == 1 and rep.placed == 0
    assert any(r["status"] == "refused" for r in rep.rows)


def test_armed_and_confirmed_places_in_order_cancel_before_sell():
    b = Spy(armed=True, positions=[Position("SPY", 10, 90.0, 100.0)],
            open_orders=[{"client_order_id": "s1", "symbol": "SPY", "side": "SELL", "order_type": "STOP",
                          "quantity": 10, "limit_price": None, "stop_price": 80.0, "status": "open"}])
    rep = run(b, Signal(Side.SELL, None, "out"), confirm=True)
    kinds = [c[0] for c in b.calls]
    assert kinds == ["preview", "cancel", "place"]
    assert b.cancelled()[0][1] == "s1"
    o = b.placed()[0][1]
    assert isinstance(o, Order) and o.side is Side.SELL and o.order_type is OrderType.MARKET and o.quantity == 10
    assert rep.placed == 1


def test_ask_hook_can_only_decline_and_is_never_called_on_dry_run():
    asked = []
    def no(order, text):
        asked.append(text)
        return False
    b = Spy(armed=True)
    rep = run(b, Signal(Side.BUY, 90.0, "go"), confirm=True, ask=no)
    assert b.placed() == [] and rep.declined == 1 and asked and "BUY 10 SPY MARKET" in asked[0]
    asked.clear()
    run(Spy(armed=True), Signal(Side.BUY, 90.0, "go"), confirm=False, ask=no)
    assert asked == []


def test_confirm_must_be_literal_true():
    b = Spy(armed=True)
    run(b, Signal(Side.BUY, 90.0, "go"), confirm="yes")
    assert b.placed() == []
