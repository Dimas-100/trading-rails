"""RunConfig from rails.toml with RAILS_* environment overrides. Nothing here can set `confirm`."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RunConfig:
    symbols: tuple[str, ...]
    dollars_per_position: float
    max_positions: int
    max_order_notional: float
    max_price_deviation: float = 0.20
    strategy: str = "sma_cross"
    strategy_params: dict = field(default_factory=dict)
    bars_lookback: int = 250
    data_source: str = "synthetic"
    paper_starting_cash: float = 100_000.0
    paper_state_path: str = "data/paper.json"
    log_path: str = "data/runs.jsonl"


_REQUIRED = ("symbols", "dollars_per_position", "max_positions", "max_order_notional")


def load_config(path: str | Path, environ=None) -> RunConfig:
    environ = os.environ if environ is None else environ
    with Path(path).open("rb") as fh:
        doc = tomllib.load(fh)
    run = dict(doc.get("run", {}))
    strat = dict(doc.get("strategy", {}))
    data = dict(doc.get("data", {}))
    paper = dict(doc.get("paper", {}))
    log = dict(doc.get("log", {}))

    def env(key, cast, current):
        raw = environ.get(f"RAILS_{key}")
        return current if raw is None or str(raw).strip() == "" else cast(str(raw).strip())

    symbols = run.get("symbols", [])
    symbols = env("SYMBOLS", lambda s: [x.strip() for x in s.split(",") if x.strip()], symbols)
    run["symbols"] = tuple(str(s).strip().upper() for s in symbols)
    run["dollars_per_position"] = env("DOLLARS_PER_POSITION", float, run.get("dollars_per_position"))
    run["max_positions"] = env("MAX_POSITIONS", int, run.get("max_positions"))
    run["max_order_notional"] = env("MAX_ORDER_NOTIONAL", float, run.get("max_order_notional"))
    missing = [k for k in _REQUIRED if run.get(k) in (None, (), [])]
    if missing:
        raise ValueError(f"rails.toml [run] is missing {missing}")
    name = env("STRATEGY", str, strat.pop("name", run.get("strategy", "sma_cross")))
    return RunConfig(
        symbols=run["symbols"],
        dollars_per_position=float(run["dollars_per_position"]),
        max_positions=int(run["max_positions"]),
        max_order_notional=float(run["max_order_notional"]),
        max_price_deviation=env("MAX_PRICE_DEVIATION", float, float(run.get("max_price_deviation", 0.20))),
        strategy=name,
        strategy_params=strat,
        bars_lookback=env("BARS_LOOKBACK", int, int(run.get("bars_lookback", 250))),
        data_source=env("DATA_SOURCE", str, str(data.get("source", "synthetic"))),
        paper_starting_cash=float(paper.get("starting_cash", 100_000.0)),
        paper_state_path=str(paper.get("state_path", "data/paper.json")),
        log_path=env("LOG_PATH", str, str(log.get("path", "data/runs.jsonl"))),
    )
