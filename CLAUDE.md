# CLAUDE.md

Broker-agnostic trading toolkit. Read `docs/safety-model.md` first.

**Hard invariant — never weaken the submit gate.** `safety.should_submit` answers True only for the literal
True; `runner.execute` is the only caller of `broker.place`/`broker.cancel`, and only after validate →
preview → `gate_decision` (confirm AND armed, each literal `True`) → the optional `ask` hook (approves only on
the literal `True`). `cli.py`'s `run_cycle` sets `confirm=True` for both `--paper` and `--live` — the paper
broker cannot lose money, so it always submits with no typed confirm — but only `--live` also passes
`ask=_typed_confirm`, so a real `--broker NAME` run without `--live` never reaches a true confirm and stays
`dry-run`. Tests pin all of this (`tests/test_safety.py`, `tests/test_runner_gate.py`, `tests/test_cli.py`).
New walls may only add strictness.

**Run / test:** `pip install -e ".[dev]"` · `pytest` · `ruff check .` · `rails --help`. Webull extra:
`pip install -e ".[dev,webull]"`. Bars are always ascending (`data.sort_bars`). Every `Broker.place` returns
`PlaceResult`. Adapters parse account/order payloads fail-closed (raise `BrokerError` on an unrecognised
shape; never an empty list for "unknown"). No price data or secrets in git (`.env`, `data/`, token dirs are
ignored).
