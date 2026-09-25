"""The CLI's live wall. An AST test proves confirm=True is passed in exactly one place (the --live
branch) together with ask=_typed_confirm, and that no argparse argument, env var or config key maps
to `confirm`. A behavioural test proves a non-TTY stdin never confirms."""
import ast
import inspect
import io
import sys

import pytest

from trading_rails import cli
from trading_rails.models import Order, OrderType, Side


def test_confirm_true_appears_once_inside_the_live_branch():
    tree = ast.parse(inspect.getsource(cli))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "confirm":
                    hits.append((node, kw))
    assert len(hits) == 1, f"confirm= must be passed exactly once, found {len(hits)}"
    node, kw = hits[0]
    assert isinstance(kw.value, ast.Name) and kw.value.id == "run_confirm"
    asks = [k for k in node.keywords if k.arg == "ask"]
    assert asks and isinstance(asks[0].value, ast.Name) and asks[0].value.id == "live_ask"


def test_live_confirm_is_derived_only_from_the_live_flag():
    src = inspect.getsource(cli)
    assert "run_confirm = True if (args.paper or args.live) else False" in src
    assert "live_ask = _typed_confirm if args.live else None" in src
    for forbidden in ('"--yes"', '"--confirm"', "RAILS_CONFIRM", 'confirm=True'):
        assert forbidden not in src, f"{forbidden} must not appear in cli.py"


def test_parser_has_no_confirm_or_yes_argument():
    parser = cli.build_parser()
    for sub in parser._subparsers._group_actions[0].choices.values():
        for action in sub._actions:
            for opt in action.option_strings:
                assert opt not in ("--yes", "-y", "--confirm")


def test_typed_confirm_refuses_without_a_tty(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("CONFIRM\n"))
    o = Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    assert cli._typed_confirm(o, "BUY 1 SPY MARKET DAY") is False
    assert "interactive" in capsys.readouterr().out.lower()


def test_typed_confirm_requires_the_exact_word(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self): return True
    o = Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    monkeypatch.setattr(sys, "stdin", Tty("confirm\n"))
    monkeypatch.setattr("builtins.input", lambda prompt="": sys.stdin.readline().strip())
    assert cli._typed_confirm(o, "x") is False
    monkeypatch.setattr(sys, "stdin", Tty("CONFIRM\n"))
    assert cli._typed_confirm(o, "x") is True


def test_backtest_and_paper_commands_run_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["backtest", "--strategy", "sma_cross", "--symbol", "SPY", "--source", "synthetic",
                     "--bars", "600"]) == 0
    out = capsys.readouterr().out
    assert "trades" in out and "max_drawdown_pct" in out
    cfg = tmp_path / "rails.toml"
    cfg.write_text('[run]\nsymbols=["SPY"]\ndollars_per_position=1000\nmax_positions=1\nmax_order_notional=2000\n'
                   '[strategy]\nname="sma_cross"\nfast=5\nslow=20\n[data]\nsource="synthetic"\n')
    assert cli.main(["run", "--config", str(cfg), "--paper", "--as-of", "2019-06-03"]) == 0
    assert cli.main(["paper", "status", "--config", str(cfg)]) == 0
    assert "cash" in capsys.readouterr().out
    assert (tmp_path / "data" / "paper.json").exists()


def test_run_requires_exactly_one_of_paper_or_broker(tmp_path):
    cfg = tmp_path / "rails.toml"
    cfg.write_text('[run]\nsymbols=["SPY"]\ndollars_per_position=1\nmax_positions=1\nmax_order_notional=1\n')
    with pytest.raises(SystemExit):
        cli.main(["run", "--config", str(cfg)])
    with pytest.raises(SystemExit):
        cli.main(["run", "--config", str(cfg), "--paper", "--broker", "webull"])
    with pytest.raises(SystemExit):
        cli.main(["run", "--config", str(cfg), "--paper", "--live"])   # --live needs --broker


def test_run_mode_wiring(tmp_path, monkeypatch):
    """--paper: confirm True, no ask; --broker: confirm False; --broker --live: confirm True with the typed wall."""
    cfg = tmp_path / "rails.toml"
    cfg.write_text('[run]\nsymbols=["SPY"]\ndollars_per_position=1000\nmax_positions=1\nmax_order_notional=2000\n')
    seen = []
    class FakeBroker:
        name = "fake"
        def armed(self): return False
        def bars(self, symbol, n, as_of=None): return []
        def last_price(self, symbol, as_of=None): return 1.0
    monkeypatch.setattr(cli, "_broker_from_name", lambda name, config: FakeBroker())
    runner_mod = __import__("trading_rails.runner", fromlist=["CycleReport"])
    monkeypatch.setattr(cli, "execute", lambda *a, **kw: seen.append(kw) or runner_mod.CycleReport(as_of=None))
    monkeypatch.chdir(tmp_path)
    cli.main(["run", "--config", str(cfg), "--paper"])
    cli.main(["run", "--config", str(cfg), "--broker", "fake"])
    cli.main(["run", "--config", str(cfg), "--broker", "fake", "--live"])
    assert [k["confirm"] for k in seen] == [True, False, True]
    assert seen[0]["ask"] is None and seen[1]["ask"] is None and seen[2]["ask"] is cli._typed_confirm


def test_paper_replay_actually_places_and_fills(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "rails.toml"
    cfg.write_text('[run]\nsymbols=["SPY"]\ndollars_per_position=1000\nmax_positions=1\nmax_order_notional=2000\n'
                   '[strategy]\nname="sma_cross"\nfast=5\nslow=20\n[data]\nsource="synthetic"\n')
    # Find the first BUY bar of this config on the seed-7 synthetic SPY series, then replay it and the next day.
    from trading_rails.data import SyntheticBars
    from trading_rails.strategy import get_strategy
    src, strat = SyntheticBars(), get_strategy("sma_cross", fast=5, slow=20)
    bars = src.bars("SPY", 400)

    def sig(i):
        return strat.on_bars(bars[max(0, i - 249):i + 1])
    buy_ts = next(bars[i].ts for i in range(25, len(bars))
                  if sig(i).action is not None and sig(i).action.value == "BUY")
    next_ts = bars[[b.ts for b in bars].index(buy_ts) + 1].ts
    assert cli.main(["run", "--config", str(cfg), "--paper", "--as-of", buy_ts]) == 0
    first = capsys.readouterr().out
    assert "placed=1" in first and "mode=paper" in first
    assert cli.main(["run", "--config", str(cfg), "--paper", "--as-of", next_ts]) == 0
    capsys.readouterr()
    assert cli.main(["paper", "status", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "SPY" in out and "cash 100,000.00" not in out


def test_as_of_is_paper_only_and_live_needs_a_pricing_broker(tmp_path, monkeypatch):
    cfg = tmp_path / "rails.toml"
    cfg.write_text('[run]\nsymbols=["SPY"]\ndollars_per_position=1\nmax_positions=1\nmax_order_notional=1\n')
    class NoBars:
        name = "nobars"
        def armed(self): return False
    monkeypatch.setattr(cli, "_broker_from_name", lambda name, config: NoBars())
    with pytest.raises(SystemExit, match="paper-replay"):
        cli.main(["run", "--config", str(cfg), "--broker", "nobars", "--as-of", "2024-01-02"])
    with pytest.raises(SystemExit, match="BarSource"):
        cli.main(["run", "--config", str(cfg), "--broker", "nobars", "--live"])


def test_live_flag_cannot_be_abbreviated(tmp_path, monkeypatch):
    cfg = tmp_path / "rails.toml"
    cfg.write_text('[run]\nsymbols=["SPY"]\ndollars_per_position=1\nmax_positions=1\nmax_order_notional=1\n')
    monkeypatch.setattr(cli, "_broker_from_name", lambda name, config: (_ for _ in ()).throw(
        AssertionError("must not build a broker: parsing should fail first")))
    with pytest.raises(SystemExit):
        cli.main(["run", "--config", str(cfg), "--broker", "x", "--li"])


def test_typed_confirm_treats_eof_and_interrupt_as_decline(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self): return True
    o = Order(symbol="SPY", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    monkeypatch.setattr(sys, "stdin", Tty(""))

    def raise_eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", raise_eof)
    assert cli._typed_confirm(o, "x") is False

    def raise_interrupt(prompt=""):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", raise_interrupt)
    assert cli._typed_confirm(o, "x") is False
