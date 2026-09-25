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


def test_webull_detection_needs_webull_core(monkeypatch):
    import importlib.util
    seen = []

    def missing_parent(name, *a):
        seen.append(name)
        raise ModuleNotFoundError(f"No module named {name.split('.')[0]!r}")
    monkeypatch.setattr(importlib.util, "find_spec", missing_parent)
    assert adapters._webull_installed() is False and seen == ["webull.core"]
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None)   # a `webull` without `core`
    assert adapters._webull_installed() is False
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object())
    assert adapters._webull_installed() is True


def test_template_adapter_fails_closed_until_implemented():
    from trading_rails.adapters.template import TemplateBroker
    from trading_rails.broker import BrokerError
    b = TemplateBroker(client=None)
    assert b.armed() is False
    for method in (b.open_orders, b.positions, b.balance, b.account_id):
        with pytest.raises(BrokerError, match="implement"):
            method()
