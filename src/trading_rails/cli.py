"""`rails` command line. Dry-run is the default everywhere. `--live` is the ONLY way a True confirm reaches
the runner, and it always travels with ask=_typed_confirm, which needs an interactive terminal and the
literal word CONFIRM per order. There is deliberately no yes-flag."""
from __future__ import annotations

import argparse
import sys

from . import backtest
from .config import RunConfig, load_config
from .data import CsvBars, SyntheticBars
from .models import Order
from .paper import PaperBroker
from .runner import execute
from .strategy import get_strategy, list_strategies


def _typed_confirm(order: Order, text: str) -> bool:
    """The typed-CONFIRM wall. False on a non-interactive stdin; True only for the exact word."""
    if not sys.stdin.isatty():
        print("refusing to submit: --live needs an interactive terminal (stdin is not a TTY)")
        return False
    try:
        answer = input(f"Type CONFIRM to submit {text}: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip() == "CONFIRM"


def _source_from_spec(spec: str, seed: int = 7, n: int = 1500):
    if spec == "synthetic":
        return SyntheticBars(seed=seed, n=n)
    if spec.startswith("csv:"):
        return CsvBars(spec[4:])
    raise SystemExit(f"unknown data source {spec!r}: use 'synthetic' or 'csv:PATH'")


def _broker_from_name(name: str, config: RunConfig):
    from . import adapters
    return adapters.get_broker(name, config)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rails", description="Broker-agnostic trading toolkit with safety rails.",
                                allow_abbrev=False)
    sub = p.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser("backtest", help="replay a strategy over bars (offline)", allow_abbrev=False)
    bt.add_argument("--strategy", default="sma_cross")
    bt.add_argument("--symbol", default="SPY")
    bt.add_argument("--source", default="synthetic", help="synthetic | csv:PATH")
    bt.add_argument("--seed", type=int, default=7)
    bt.add_argument("--bars", type=int, default=1500)
    bt.add_argument("--dollars", type=float, default=None, help="dollars per trade (default: all equity)")
    bt.add_argument("--lookback", type=int, default=250)
    bt.add_argument("--trades", action="store_true", help="print the trade list")
    bt.add_argument("--list", action="store_true", help="list registered strategies and exit")
    bt.set_defaults(func=run_backtest)

    run = sub.add_parser("run", help="one cycle: signals -> orders -> validate -> preview -> (gate) -> place",
                         allow_abbrev=False)
    run.add_argument("--config", default="rails.toml")
    run.add_argument("--paper", action="store_true", help="use the built-in PaperBroker (always dry-run safe)")
    run.add_argument("--broker", default=None, help="adapter name from `rails brokers` (dry-run unless --live)")
    run.add_argument("--live", action="store_true",
                     help="submit to the real broker; prompts for a typed CONFIRM per order; "
                          "needs RAILS_LIVE_ENABLED=1")
    run.add_argument("--as-of", default=None, help="ISO date: use bars up to this date (paper replay)")
    run.set_defaults(func=run_cycle)

    paper = sub.add_parser("paper", help="paper account tools", allow_abbrev=False)
    psub = paper.add_subparsers(dest="paper_cmd", required=True)
    st = psub.add_parser("status", help="cash, positions, open orders", allow_abbrev=False)
    st.add_argument("--config", default="rails.toml")
    st.set_defaults(func=paper_status)

    br = sub.add_parser("brokers", help="list broker adapters", allow_abbrev=False)
    br.set_defaults(func=list_brokers)
    return p


def run_backtest(args) -> int:
    if args.list:
        print("\n".join(list_strategies()))
        return 0
    source = _source_from_spec(args.source, seed=args.seed, n=args.bars)
    bars = source.bars(args.symbol, args.bars)
    strategy = get_strategy(args.strategy)
    result = backtest.run(strategy, bars, dollars_per_trade=args.dollars, lookback=args.lookback)
    print(f"{args.strategy} on {args.symbol} ({len(bars)} bars, {bars[0].ts} .. {bars[-1].ts})")
    print(backtest.format_metrics(result))
    if args.trades:
        for t in result.trades:
            print(f"{t.entry_ts} -> {t.exit_ts}  {t.quantity} @ {t.entry_price:.2f} -> {t.exit_price:.2f}  "
                  f"{t.return_pct:+.2f}%  {t.reason}")
    return 0


def _paper_broker(config: RunConfig):
    source = _source_from_spec(config.data_source)
    return PaperBroker(source, config.paper_state_path, starting_cash=config.paper_starting_cash), source


def run_cycle(args) -> int:
    if bool(args.paper) == bool(args.broker):
        raise SystemExit("choose exactly one of --paper or --broker NAME")
    if args.live and not args.broker:
        raise SystemExit("--live applies to --broker NAME only")
    if args.as_of and not args.paper:
        raise SystemExit("--as-of is a paper-replay option; a broker run always prices from the broker")
    config = load_config(args.config)
    strategy = get_strategy(config.strategy, **config.strategy_params)
    if args.paper:
        broker, source = _paper_broker(config)
    else:
        broker = _broker_from_name(args.broker, config)
        source = broker if hasattr(broker, "bars") else _source_from_spec(config.data_source)
        if args.live and source is not broker:
            raise SystemExit(f"--live needs a broker that serves its own bars/prices (BarSource); "
                             f"{broker.name!r} does not")
    run_confirm = True if (args.paper or args.live) else False
    live_ask = _typed_confirm if args.live else None
    mode = "LIVE" if args.live else "dry-run"
    print(f"rails run: broker={broker.name} armed={broker.armed()} mode={mode} as_of={args.as_of or 'latest'}")
    report = execute(config, strategy, broker, source, confirm=run_confirm, ask=live_ask,
                     as_of=args.as_of, log_path=config.log_path)
    for row in report.rows:
        if row["status"] in ("signal", "ok") or row["step"] == "signal":
            continue
        print(f"  {row['symbol']:<6} {row['step']:<8} {row['status']:<11} {row['detail']}")
    print(f"placed={report.placed} dry_run={report.dry_run} refused={report.refused} declined={report.declined} "
          f"skipped={report.skipped} errors={report.errors} unprotected={report.unprotected}")
    return 0


def paper_status(args) -> int:
    config = load_config(args.config)
    broker, _ = _paper_broker(config)
    bal = broker.balance()
    print(f"cash {bal.cash:,.2f}  buying_power {bal.buying_power:,.2f}  net_liq {bal.net_liq:,.2f}")
    for p in broker.positions():
        print(f"  {p.symbol:<6} {p.quantity:>6} @ {p.avg_cost:.2f}  last {p.last_price:.2f}")
    for o in broker.open_orders():
        print(f"  open {o['side']} {o['quantity']} {o['symbol']} {o['order_type']} "
              f"limit={o['limit_price']} stop={o['stop_price']}")
    return 0


def list_brokers(args) -> int:
    from . import adapters
    for name, status in adapters.available().items():
        print(f"{name:<8} {status}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
