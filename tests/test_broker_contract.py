"""Seam test: every shipped broker's place() answers a PlaceResult (the runner branches only on .placed)."""
from tests.test_adapters_webull import FakeData, FakeTrade
from trading_rails.adapters import webull as wb
from trading_rails.data import SyntheticBars
from trading_rails.models import Order, OrderType, PlaceResult, Side
from trading_rails.paper import PaperBroker


def test_paper_and_webull_place_return_placeresult(tmp_path):
    o = Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    paper = PaperBroker(SyntheticBars(n=10), tmp_path / "p.json", starting_cash=1000.0)
    paper.sync()
    assert isinstance(paper.place(o), PlaceResult)
    webull = wb.WebullBroker(FakeTrade(), FakeData(), environ={"RAILS_LIVE_ENABLED": "1"})
    assert isinstance(webull.place(o), PlaceResult)
