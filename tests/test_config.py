import pytest

from trading_rails.config import RunConfig, load_config

TOML = """
[run]
symbols = ["spy", "qqq"]
dollars_per_position = 1000
max_positions = 2
max_order_notional = 1500
bars_lookback = 120

[strategy]
name = "sma_cross"
fast = 10
slow = 30

[data]
source = "synthetic"

[paper]
starting_cash = 5000
state_path = "data/p.json"

[log]
path = "data/r.jsonl"
"""


def test_load_config_reads_sections_and_normalises(tmp_path):
    f = tmp_path / "rails.toml"
    f.write_text(TOML)
    c = load_config(f, environ={})
    assert isinstance(c, RunConfig)
    assert c.symbols == ("SPY", "QQQ") and c.dollars_per_position == 1000.0 and c.max_positions == 2
    assert c.max_order_notional == 1500.0 and c.max_price_deviation == 0.20 and c.bars_lookback == 120
    assert c.strategy == "sma_cross" and c.strategy_params == {"fast": 10, "slow": 30}
    assert c.data_source == "synthetic" and c.paper_starting_cash == 5000.0
    assert c.paper_state_path == "data/p.json" and c.log_path == "data/r.jsonl"


def test_env_overrides_win(tmp_path):
    f = tmp_path / "rails.toml"
    f.write_text(TOML)
    env = {"RAILS_SYMBOLS": "aapl, msft", "RAILS_DOLLARS_PER_POSITION": "250", "RAILS_MAX_POSITIONS": "1",
           "RAILS_MAX_ORDER_NOTIONAL": "300", "RAILS_MAX_PRICE_DEVIATION": "0.1", "RAILS_STRATEGY": "sma_cross",
           "RAILS_BARS_LOOKBACK": "60", "RAILS_DATA_SOURCE": "csv:bars", "RAILS_LOG_PATH": "x.jsonl"}
    c = load_config(f, environ=env)
    assert c.symbols == ("AAPL", "MSFT") and c.dollars_per_position == 250.0 and c.max_positions == 1
    assert c.max_order_notional == 300.0 and c.max_price_deviation == 0.1 and c.bars_lookback == 60
    assert c.data_source == "csv:bars" and c.log_path == "x.jsonl"


def test_missing_required_keys_raise(tmp_path):
    f = tmp_path / "rails.toml"
    f.write_text("[run]\nsymbols = ['SPY']\n")
    with pytest.raises(ValueError):
        load_config(f, environ={})
