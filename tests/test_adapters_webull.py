"""Pure translation tests — no SDK, no network. The adapter module must import without the SDK
(it guards the SDK import) so these run in the base install too."""
import pytest

from trading_rails.adapters import webull as wb
from trading_rails.models import Bar, Order, OrderType, PlaceResult, Side, TimeInForce


def test_to_webull_translation_table():
    o = Order(symbol="aapl", side=Side.BUY, quantity=2, order_type=OrderType.LIMIT, limit_price=100.0,
              client_order_id="abc")
    d = wb.to_webull(o)
    assert d == {"client_order_id": "abc", "combo_type": "NORMAL", "symbol": "AAPL", "instrument_type": "EQUITY",
                 "market": "US", "order_type": "LIMIT", "quantity": "2", "support_trading_session": "CORE",
                 "side": "BUY", "time_in_force": "DAY", "entrust_type": "QTY", "limit_price": "100.00"}
    stop = Order(symbol="SPY", side=Side.SELL, quantity=1, order_type=OrderType.STOP, stop_price=95.5,
                 time_in_force=TimeInForce.GTC)
    ds = wb.to_webull(stop)
    assert ds["order_type"] == "STOP_LOSS" and ds["stop_price"] == "95.50" and "limit_price" not in ds
    assert ds["time_in_force"] == "GTC"
    sl = Order(symbol="SPY", side=Side.SELL, quantity=1, order_type=OrderType.STOP_LIMIT, stop_price=95.0,
               limit_price=94.0)
    assert wb.to_webull(sl)["order_type"] == "STOP_LOSS_LIMIT"
    mkt = Order(symbol="SPY", side=Side.SELL, quantity=1, order_type=OrderType.MARKET)
    assert "limit_price" not in wb.to_webull(mkt) and "stop_price" not in wb.to_webull(mkt)


def test_bars_from_rows_is_ascending_regardless_of_input_order():
    raw = [{"time": "2024-01-03", "open": "3", "high": "4", "low": "2", "close": "3.5", "volume": "10"},
           {"time": "2024-01-02", "open": "2", "high": "3", "low": "1", "close": "2.5", "volume": "10"}]
    bars = wb.bars_from_rows(raw)
    assert [b.ts for b in bars] == ["2024-01-02", "2024-01-03"] and isinstance(bars[0], Bar)
    assert wb.bars_from_rows({"data": list(reversed(raw))}) == bars     # dict envelope, already ascending
    assert wb.bars_from_rows([{"t": "2024-01-02", "o": "1", "h": "1", "l": "1", "c": None}]) == []


def test_position_and_balance_parsing():
    p = wb.position_from_row({"items": [{"symbol": "AAPL"}], "quantity": "3", "cost_price": "150.5",
                              "last_price": "160"})
    assert p.symbol == "AAPL" and p.quantity == 3 and p.avg_cost == 150.5 and p.last_price == 160.0
    assert wb.position_from_row({"ticker": "X", "qty": "0", "unit_cost": "1"}) is None
    with pytest.raises(wb.BrokerError):
        wb.position_from_row({"quantity": "2"})
    b = wb.balance_from_body({"total_net_liquidation_value": "1000.5",
                              "account_currency_assets": [{"settled_cash": "400", "buying_power": "800"}]})
    assert b.net_liq == 1000.5 and b.cash == 400.0 and b.buying_power == 800.0
    b2 = wb.balance_from_body({"account_currency_assets": [{"net_liquidation_value": "5", "cash_balance": "5"}]})
    assert b2.net_liq == 5.0 and b2.cash == 5.0 and b2.buying_power == 5.0


def test_open_order_and_account_parsing():
    row = {"client_order_id": "c1", "status": "WORKING", "items": [
        {"symbol": "SPY", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "4", "stop_price": "90"}]}
    o = wb.open_order_from_row(row)
    assert o == {"client_order_id": "c1", "symbol": "SPY", "side": "SELL", "order_type": "STOP",
                 "quantity": 4, "limit_price": None, "stop_price": 90.0, "status": "WORKING"}
    assert wb.open_order_from_row({"status": "x"}) is None
    rows = [{"account_id": "crypto", "account_class": "CRYPTO"},
            {"account_id": "cash1", "account_class": "INDIVIDUAL_CASH"}]
    assert wb.pick_cash_account(rows) == "cash1"
    with pytest.raises(wb.BrokerError):
        wb.pick_cash_account([{"account_id": "crypto", "account_class": "CRYPTO"}])


def test_arming_and_token_dir_are_env_driven(tmp_path):
    assert wb.is_armed({}) is False and wb.is_armed({"RAILS_LIVE_ENABLED": "1"}) is True
    assert wb.is_armed({"RAILS_LIVE_ENABLED": "true"}) is False
    assert wb.token_dir({"WEBULL_OPENAPI_TOKEN_DIR": str(tmp_path)}) == str(tmp_path.resolve())
    assert wb.token_dir({}).endswith(".webull-tokens")


class FakeRes:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, str(body)
    def json(self): return self._body


class FakeOrders:
    def __init__(self):
        self.calls = []

    def preview_order(self, acct, orders):
        self.calls.append(("preview", orders))
        return FakeRes({"ok": 1})

    def place_order(self, acct, orders):
        self.calls.append(("place", orders))
        return FakeRes({"order_id": "o9", "client_order_id": orders[0]["client_order_id"]})

    def cancel_order(self, acct, cid):
        self.calls.append(("cancel", cid))
        return FakeRes({"cancelled": True})

    def get_order_open(self, acct):
        return FakeRes([])


class FakeAccounts:
    def get_account_list(self): return FakeRes([{"account_id": "cash1", "account_class": "INDIVIDUAL_CASH"}])
    def get_account_balance(self, acct): return FakeRes({"total_net_liquidation_value": "10"})
    def get_account_position(self, acct): return FakeRes([])


class FakeTrade:
    def __init__(self): self.order_v2, self.account_v2 = FakeOrders(), FakeAccounts()


class FakeMarket:
    def get_history_bar(self, symbol, category, timespan, count="250"):
        return FakeRes([{"time": "2024-01-02", "open": "1", "high": "1", "low": "1", "close": "1"}])
    def get_snapshot(self, symbol, category): return FakeRes([{"price": "123.4"}])


class FakeData:
    def __init__(self): self.market_data = FakeMarket()


def test_broker_methods_through_fake_sdk_clients():
    b = wb.WebullBroker(FakeTrade(), FakeData(), environ={"RAILS_LIVE_ENABLED": "1"})
    assert b.name == "webull" and b.armed() is True and b.account_id() == "cash1"
    o = Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    assert b.preview(o) == {"ok": 1}
    res = b.place(o)
    assert isinstance(res, PlaceResult) and res.placed and res.order_id == "o9"
    assert b._trade.order_v2.calls[-1][1][0]["symbol"] == "SPY"
    assert b.cancel("c")["cancelled"] is True
    assert b.balance().net_liq == 10.0 and b.positions() == [] and b.open_orders() == []
    assert b.bars("SPY", 5)[0].ts == "2024-01-02" and b.last_price("SPY") == 123.4
    assert wb.WebullBroker(FakeTrade(), FakeData(), environ={}).armed() is False


def test_non_200_becomes_broker_error():
    class BadOrders(FakeOrders):
        def preview_order(self, acct, orders): return FakeRes({"msg": "bad"}, status=400)
    t = FakeTrade()
    t.order_v2 = BadOrders()
    b = wb.WebullBroker(t, FakeData(), environ={})
    with pytest.raises(wb.BrokerError):
        b.preview(Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET))


def test_positions_and_open_orders_fail_closed_on_unknown_payloads():
    class Env(FakeAccounts):
        def get_account_position(self, acct):
            return FakeRes({"data": [{"symbol": "SPY", "quantity": "2", "cost_price": "1"}]})
    t = FakeTrade()
    t.account_v2 = Env()
    b = wb.WebullBroker(t, FakeData(), environ={})
    assert [p.symbol for p in b.positions()] == ["SPY"]                    # data envelope is unwrapped

    class Weird(FakeAccounts):
        def get_account_position(self, acct): return FakeRes({"weird": True})
    t2 = FakeTrade()
    t2.account_v2 = Weird()
    with pytest.raises(wb.BrokerError):
        wb.WebullBroker(t2, FakeData(), environ={}).positions()           # non-list, non-envelope body

    class NoSym(FakeOrders):
        def get_order_open(self, acct): return FakeRes("nope")
    t3 = FakeTrade()
    t3.order_v2 = NoSym()
    with pytest.raises(wb.BrokerError):
        wb.WebullBroker(t3, FakeData(), environ={}).open_orders()


def test_rows_with_quantity_but_no_symbol_raise():
    with pytest.raises(wb.BrokerError):
        wb.position_from_row({"quantity": "3", "cost_price": "10"})
    assert wb.position_from_row({"quantity": "0"}) is None
    with pytest.raises(wb.BrokerError):
        wb.open_order_from_row({"client_order_id": "c9", "side": "SELL", "quantity": "1"})
    assert wb.open_order_from_row({"status": "x"}) is None


def test_place_refuses_when_not_armed():
    b = wb.WebullBroker(FakeTrade(), FakeData(), environ={})
    with pytest.raises(wb.BrokerError, match="not armed"):
        b.place(Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET))
    assert b._trade.order_v2.calls == []


def test_from_env_loads_only_the_cwd_dotenv_never_a_parent(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    pytest.importorskip("webull.core")
    import os
    key = "RAILS_TEST_DOTENV_SENTINEL"
    child = tmp_path / "child"
    child.mkdir()
    (tmp_path / ".env").write_text(f"{key}=parent\n")
    monkeypatch.chdir(child)
    try:
        with pytest.raises(wb.BrokerError, match="WEBULL_APP_KEY"):
            wb.WebullBroker.from_env(environ={})
        assert key not in os.environ                        # the parent directory's .env was not read
        (child / ".env").write_text(f"{key}=cwd\n")
        with pytest.raises(wb.BrokerError, match="WEBULL_APP_KEY"):
            wb.WebullBroker.from_env(environ={})
        assert os.environ.get(key) == "cwd"                 # the working directory's .env was
    finally:
        os.environ.pop(key, None)
