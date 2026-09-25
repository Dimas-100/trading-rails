import json

from trading_rails.broker import Broker
from trading_rails.models import Bar, Order, OrderType, PlaceResult, Side, TimeInForce
from trading_rails.paper import PaperBroker


class Seq:
    """A bar source whose 'newest bar' advances with as_of."""
    def __init__(self, bars):
        self._bars = bars

    def bars(self, symbol, n, as_of=None):
        rows = [b for b in self._bars if as_of is None or b.ts <= as_of]
        return rows[-n:]

    def last_price(self, symbol, as_of=None):
        return self.bars(symbol, 1, as_of)[-1].close


BARS = [
    Bar("2024-01-02", 100, 101, 99, 100),
    Bar("2024-01-03", 102, 104, 101, 103),
    Bar("2024-01-04", 95, 96, 90, 92),
]


def broker(tmp_path, cash=10_000.0):
    return PaperBroker(Seq(BARS), tmp_path / "paper.json", starting_cash=cash)


def test_is_a_broker_and_always_armed(tmp_path):
    b = broker(tmp_path)
    assert isinstance(b, Broker) and b.armed() is True and b.account_id() == "paper"
    assert b.balance().cash == 10_000.0 and b.positions() == []


def test_market_buy_reserves_then_fills_on_next_bar(tmp_path):
    b = broker(tmp_path)
    b.sync(as_of="2024-01-02")
    res = b.place(Order(symbol="SPY", side=Side.BUY, quantity=10, order_type=OrderType.MARKET))
    assert isinstance(res, PlaceResult) and res.placed and res.order_id
    assert b.balance().buying_power == 10_000.0 - 10 * 100   # reserved at last price
    assert b.sync(as_of="2024-01-02") == []                    # same bar: nothing fills
    fills = b.sync(as_of="2024-01-03")
    assert len(fills) == 1 and fills[0].price == 102.0 and fills[0].quantity == 10
    pos = b.positions()
    assert pos[0].symbol == "SPY" and pos[0].quantity == 10 and pos[0].avg_cost == 102.0
    assert b.balance().cash == 10_000.0 - 1020.0 and b.open_orders() == []


def test_state_persists_across_instances(tmp_path):
    b = broker(tmp_path)
    b.sync(as_of="2024-01-02")
    b.place(Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET))
    again = PaperBroker(Seq(BARS), tmp_path / "paper.json")
    assert len(again.open_orders()) == 1
    assert json.loads((tmp_path / "paper.json").read_text())["cash"] == 10_000.0


def test_unaffordable_buy_is_rejected_in_preview_and_place(tmp_path):
    b = broker(tmp_path, cash=500.0)
    b.sync(as_of="2024-01-02")
    o = Order(symbol="SPY", side=Side.BUY, quantity=10, order_type=OrderType.MARKET)
    assert b.preview(o)["ok"] is False
    res = b.place(o)
    assert res.placed is False and "cash" in res.raw["error"]
    assert b.balance().cash == 500.0 and b.open_orders() == []


def test_sell_more_than_held_is_rejected(tmp_path):
    b = broker(tmp_path)
    b.sync(as_of="2024-01-02")
    res = b.place(Order(symbol="SPY", side=Side.SELL, quantity=1, order_type=OrderType.MARKET))
    assert res.placed is False and "shares" in res.raw["error"]


def test_stop_sell_gap_through_and_gtc_rests(tmp_path):
    b = broker(tmp_path)
    b.sync(as_of="2024-01-02")
    b.place(Order(symbol="SPY", side=Side.BUY, quantity=10, order_type=OrderType.MARKET))
    b.sync(as_of="2024-01-03")
    stop = Order(symbol="SPY", side=Side.SELL, quantity=10, order_type=OrderType.STOP, stop_price=98.0,
                 time_in_force=TimeInForce.GTC)
    assert b.place(stop).placed
    assert b.sync(as_of="2024-01-03") == []
    assert len(b.open_orders()) == 1 and b.open_orders()[0]["order_type"] == "STOP"
    fills = b.sync(as_of="2024-01-04")
    assert len(fills) == 1 and fills[0].price == 95.0             # gapped through the 98 stop
    assert b.positions() == [] and b.balance().cash == 10_000.0 - 1020.0 + 950.0


def test_day_limit_expires_when_it_does_not_fill(tmp_path):
    b = broker(tmp_path)
    b.sync(as_of="2024-01-02")
    b.place(Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.LIMIT, limit_price=95.0))
    assert b.sync(as_of="2024-01-03") == []                       # low 101 never touches 95
    assert b.open_orders() == [] and b.balance().buying_power == 10_000.0


def test_cancel_releases_reservation(tmp_path):
    b = broker(tmp_path)
    b.sync(as_of="2024-01-02")
    res = b.place(Order(symbol="SPY", side=Side.BUY, quantity=2, order_type=OrderType.MARKET))
    cid = b.open_orders()[0]["client_order_id"]
    assert b.cancel(cid)["cancelled"] is True and b.balance().buying_power == 10_000.0
    assert b.cancel("nope")["cancelled"] is False
    assert res.placed
