# trading-rails

A small, broker-agnostic trading toolkit whose whole point is that **a real-money order is hard to send by
accident**. Strategies see bars and emit signals; the runner turns signals into orders and pushes every one
through validation, a broker preview, and a gate that submits only when the safety rails all agree.
Bring your own broker by implementing two small protocols; a paper broker and a Webull adapter ship in the box.

## What you get

- **`safety.py`** — pure order validation (side/type/price rules, fat-finger guard, wrong-side stop guard,
  per-order notional cap) and the single-factor submit gate `should_submit(confirm)`.
- **`Broker` / `BarSource` protocols** — execution+account and data seams. `PaperBroker` (offline, JSON state)
  and `WebullBroker` (optional extra) implement them; `adapters/template.py` shows how to add another.
- **One fill model** shared by the paper broker and the backtest, so paper results and backtests agree.
- **A generic strategy** (`sma_cross`: 20/50 SMA crossover, long only, ATR stop) as a stand-in — replace it.
- **`rails` CLI** — `backtest`, `run --paper`, `run --broker NAME` (dry-run), `run --broker NAME --live`.

## Quickstart (offline, no credentials)

```bash
git clone <this repo> && cd trading-rails
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
rails backtest --strategy sma_cross --symbol SPY --source synthetic
cp rails.example.toml rails.toml
rails run --config rails.toml --paper --as-of 2018-05-03
rails run --config rails.toml --paper --as-of 2018-05-04
rails paper status --config rails.toml
```

The backtest sizes each trade from all equity by default; for paper and backtest to agree, pass
`rails backtest --dollars` equal to `dollars_per_position`.

`--as-of` replays the paper account one day at a time on the synthetic series. `2018-05-03` / `2018-05-04` are
consecutive trading days on the seeded synthetic series (`rails.example.toml`'s default symbols, seed and
`sma_cross` 20/50 settings): the first is the earliest day any symbol (IWM) gets a BUY signal, so the first
`run --paper` call places an entry (`placed=1`); the second day's `run --paper` call fills that entry (the
paper broker fills against the next bar) and adds a protective stop, so `rails paper status` then shows an
open IWM position. Drop `--as-of` when your data source is real (a CSV you refresh, or a broker adapter) and
schedule `rails run` after the close with cron or Task Scheduler.
If you later switch the same paper account from `--as-of` replays to daily runs on real data, start a fresh
`data/paper.json` first; a state file that mixes the two prices and fills against the wrong bars.
`rails run` exits 1 when any symbol errored and 2 when a position was left without its stop — wire your
scheduler's alert to a non-zero exit.

## The safety model (read this before `--live`)

`--paper` and `--broker` are different worlds. **`--paper` always submits** — the CLI passes `confirm=True`
for a paper run because `PaperBroker` is JSON-backed play money that can never place a real order, so there is
nothing for a dry-run to protect and no typed CONFIRM to ask for. `--paper` and `--broker` are mutually
exclusive. Everything below is about a REAL broker (`--broker NAME`).

Three walls, and each one only adds strictness; none replaces another.

1. **Dry-run default.** `rails run --broker NAME` (without `--live`) validates, previews and logs against your
   real account — and never submits — because the literal `True` never reaches `should_submit` on that path.
2. **Broker arming.** `WebullBroker.armed()` is `RAILS_LIVE_ENABLED=1` in the environment. The runner refuses
   to submit to an unarmed broker even with confirm; the adapter's own `place()` refuses independently too.
3. **Typed CONFIRM.** `--live` prints every preview — including the last price and the estimated notional —
   and then asks, per order, for the word `CONFIRM` on an interactive terminal. There is no `--yes` flag, and
   a test proves no argument, env var or config key maps to confirm.

Details, including the exit-path failure handling and the `PlaceResult.placed` contract: `docs/safety-model.md`.

## Going live with Webull

```bash
pip install -e ".[dev,webull]"
cp .env.example .env            # fill WEBULL_APP_KEY / WEBULL_APP_SECRET
rails run --config rails.toml --broker webull            # dry-run: validate + preview against your account
RAILS_LIVE_ENABLED=1 rails run --config rails.toml --broker webull --live   # previews, then CONFIRM per order
```

A live run prices only from the broker itself: `--as-of` is a paper-replay option and is refused outright for
a `--broker` run, and `--live` is refused unless the broker implements `BarSource` (serves its own `bars()`
and `last_price()`) — the Webull adapter does both, so a broker adapter without them can run dry-run and
`--paper`-backed testing but can never go `--live`. The adapter also loads a `.env` from the CURRENT WORKING
DIRECTORY (`python-dotenv`) before reading its config, so a `RAILS_LIVE_ENABLED=1` line sitting in that `.env`
arms the adapter on its own — it doesn't submit anything by itself (that still needs `--live`'s typed CONFIRM),
but it silently removes wall 2, leaving only the typed CONFIRM standing between a careless `--live` run and a
real order. Keep `RAILS_LIVE_ENABLED=0` in `.env` unless you deliberately mean to arm.

Notes: a developer-portal app key is production-only; a PaperTrade-portal key works against
`WEBULL_HOST=api.sandbox.webull.com`. Market data needs the free "Nasdaq Basic – Non Display" OpenAPI
entitlement (the adapter's error message says so). The SDK keeps its 2FA token in `WEBULL_OPENAPI_TOKEN_DIR`;
use an absolute path if you run from more than one directory. `positions()`/`open_orders()` fail closed — an
unrecognised account payload raises rather than reading as "flat" — and the open-order read asks for one
100-row page and refuses to trade when a full page comes back (the view may be partial), so keep fewer than
100 orders working.

## Data

No price data ships in the repo. `SyntheticBars` (seeded random walk with trend regimes) powers the demo;
`CsvBars` reads `data/SYMBOL.csv` (or one file with a `symbol` column) with columns
`date,open,high,low,close,volume` and ISO dates; the Webull adapter serves daily bars from your key.
`PaperBroker.preview`/`.place` never raise: an unpriceable symbol is rejected with `placed=False` instead of an
exception, and a BUY whose fill-time cost turns out to exceed cash is rejected at `sync()` (recorded in the
paper state's `rejected` list) so paper cash can never go negative.

## Extending

- Another broker: `docs/adding-a-broker.md` (implement 8 + 2 methods, register the name).
- Another strategy: `docs/adding-a-strategy.md` (`warmup()`, `on_bars(bars) -> Signal`, `@register`).

## Disclaimer

This is software, not investment advice. Trading loses money. Real-money use is entirely at your own risk;
the authors accept no liability. Provided as-is under the MIT license.
