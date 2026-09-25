import json

from trading_rails.config import RunConfig
from trading_rails.models import Balance, Bar, OrderType, PlaceResult, Position, Side, Signal, TimeInForce
from trading_rails.runner import execute


class Spy:
    name = "spy"
    def __init__(self, positions=(), open_orders=(), boom=()):
        self._positions, self._open, self.boom = list(positions), list(open_orders), set(boom)
        self.calls = []
    def armed(self): return True
    def account_id(self): return "spy"
    def preview(self, order):
        if order.symbol in self.boom:
            raise RuntimeError("broker down")
        self.calls.append(("preview", order))
        return {}
    def place(self, order):
        self.calls.append(("place", order))
        return PlaceResult(True, "1")
    def cancel(self, cid):
        self.calls.append(("cancel", cid))
        return {}
    def open_orders(self): return self._open
    def positions(self): return self._positions
    def balance(self): return Balance(10_000.0, 10_000.0, 10_000.0)
    def placed(self): return [c[1] for c in self.calls if c[0] == "place"]


class PerSymbol:
    name = "per"
    def __init__(self, signals): self.signals = signals
    def warmup(self): return 1
    def on_bars(self, bars): return self.signals[bars[-1].volume]   # volume smuggles the symbol tag


class Bars:
    def __init__(self, prices): self.prices = prices
    def bars(self, symbol, n, as_of=None):
        p = self.prices[symbol]
        return [Bar("2024-01-02", p, p, p, p, volume=hash(symbol) % 1000)]
    def last_price(self, symbol, as_of=None): return self.prices[symbol]


def tag(symbol): return hash(symbol) % 1000


def cfg(**over):
    base = dict(symbols=("AAA", "BBB"), dollars_per_position=1000.0, max_positions=1, max_order_notional=5000.0)
    base.update(over)
    return RunConfig(**base)


def test_entry_sizing_floor_and_slot_cap():
    b = Spy()
    strat = PerSymbol({tag("AAA"): Signal(Side.BUY, 90.0, ""), tag("BBB"): Signal(Side.BUY, 9.0, "")})
    rep = execute(cfg(), strat, b, Bars({"AAA": 333.0, "BBB": 10.0}), confirm=True, log_path=None)
    placed = b.placed()
    assert len(placed) == 1 and placed[0].symbol == "AAA" and placed[0].quantity == 3   # floor(1000/333)
    assert placed[0].order_type is OrderType.MARKET and placed[0].time_in_force is TimeInForce.DAY
    assert rep.skipped >= 1 and any("slots" in r["detail"] for r in rep.rows if r["status"] == "skipped")


def test_quantity_zero_is_skipped_not_placed():
    b = Spy()
    strat = PerSymbol({tag("AAA"): Signal(Side.BUY, None, ""), tag("BBB"): Signal(None, None, "")})
    rep = execute(cfg(), strat, b, Bars({"AAA": 5000.0, "BBB": 1.0}), confirm=True, log_path=None)
    assert b.placed() == [] and any("quantity 0" in r["detail"] for r in rep.rows)


def test_protect_places_gtc_stop_only_when_none_rests():
    pos = [Position("AAA", 5, 100.0, 100.0), Position("BBB", 5, 100.0, 100.0)]
    resting = [{"client_order_id": "x", "symbol": "BBB", "side": "SELL", "order_type": "STOP", "quantity": 5,
                "limit_price": None, "stop_price": 90.0, "status": "open"}]
    b = Spy(positions=pos, open_orders=resting)
    strat = PerSymbol({tag("AAA"): Signal(None, 92.5, "long"), tag("BBB"): Signal(None, 92.5, "long")})
    execute(cfg(max_positions=2), strat, b, Bars({"AAA": 100.0, "BBB": 100.0}), confirm=True, log_path=None)
    placed = b.placed()
    assert len(placed) == 1 and placed[0].symbol == "AAA"
    assert placed[0].order_type is OrderType.STOP and placed[0].stop_price == 92.5
    assert placed[0].time_in_force is TimeInForce.GTC and placed[0].quantity == 5


def test_working_buy_counts_as_a_slot_and_blocks_a_second_entry():
    working = [{"client_order_id": "w", "symbol": "AAA", "side": "BUY", "order_type": "MARKET", "quantity": 3,
                "limit_price": None, "stop_price": None, "status": "open"}]
    b = Spy(open_orders=working)
    strat = PerSymbol({tag("AAA"): Signal(Side.BUY, None, ""), tag("BBB"): Signal(Side.BUY, None, "")})
    execute(cfg(), strat, b, Bars({"AAA": 100.0, "BBB": 100.0}), confirm=True, log_path=None)
    assert b.placed() == []


def test_invalid_order_is_logged_and_skipped_others_proceed():
    b = Spy()
    strat = PerSymbol({tag("AAA"): Signal(Side.BUY, None, ""), tag("BBB"): Signal(Side.BUY, None, "")})
    rep = execute(cfg(max_positions=2, max_order_notional=950.0), strat, b, Bars({"AAA": 100.0, "BBB": 300.0}),
                  confirm=True, log_path=None)
    # AAA: 10 x 100 = 1000 > 950 cap -> invalid, skipped; BBB: 3 x 300 = 900 <= 950 -> placed
    assert [o.symbol for o in b.placed()] == ["BBB"]
    assert sum(1 for r in rep.rows if r["status"] == "invalid") == 1


def test_broker_error_on_one_symbol_does_not_abort_the_cycle(tmp_path):
    b = Spy(boom={"AAA"})
    strat = PerSymbol({tag("AAA"): Signal(Side.BUY, None, ""), tag("BBB"): Signal(Side.BUY, None, "")})
    log = tmp_path / "runs.jsonl"
    rep = execute(cfg(max_positions=2), strat, b, Bars({"AAA": 100.0, "BBB": 100.0}), confirm=True, log_path=log)
    assert [o.symbol for o in b.placed()] == ["BBB"] and rep.errors == 1
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert any(r["status"] == "error" and r["symbol"] == "AAA" for r in rows)
    assert all("ts" in r and "as_of" in r for r in rows)
