import pytest

from trading_rails import adapters
from trading_rails.config import RunConfig
from trading_rails.paper import PaperBroker

CFG = RunConfig(symbols=("SPY",), dollars_per_position=100.0, max_positions=1, max_order_notional=500.0,
                paper_state_path="data/test-paper.json")


def test_available_lists_paper_and_webull():
    avail = adapters.available()
    assert avail["paper"] == "built-in"
    assert "webull" in avail


def test_get_broker_paper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert isinstance(adapters.get_broker("paper", CFG), PaperBroker)


def test_get_broker_unknown_and_missing_extra(monkeypatch):
    with pytest.raises(KeyError):
        adapters.get_broker("nope", CFG)
    monkeypatch.setattr(adapters, "_webull_installed", lambda: False)
    with pytest.raises(ImportError) as exc:
        adapters.get_broker("webull", CFG)
    assert "[webull]" in str(exc.value)
