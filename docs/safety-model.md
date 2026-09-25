# Safety model

## The gate

`safety.should_submit(confirm)` returns `confirm is True`. It is single-factor on purpose: the dry-run default
is what prevents an accidental order, and a second factor that *replaced* confirm would be one more thing to
misconfigure. Anything else that guards submission stands in FRONT of this function and may only add
strictness (`... and something`), never remove the confirm requirement.

`cli.py`'s `run_cycle` passes `confirm=True` for BOTH `--paper` and `--live` — the paper broker cannot lose
money, so a paper run always submits with no typed CONFIRM — but only `--live` also passes `ask=_typed_confirm`
to the runner. A real `--broker NAME` run without `--live` never sets confirm True, so `gate_decision` stays
`dry-run` regardless of anything else in the environment or config.

## Three walls, in order (real brokers only)

| Wall | Where | What it checks |
|---|---|---|
| Dry-run default | `runner.gate_decision` | `should_submit(confirm)`; anything but the literal True is `dry-run` |
| Broker arming | `runner.gate_decision` | `broker.armed()` **is** the literal `True`; a confirmed submit to an unarmed broker (or one whose `armed()` returns merely something truthy) is `refused` |
| Typed CONFIRM | `cli._typed_confirm` via the runner's `ask` hook | interactive terminal + the exact word, per order, with the preview text showing the last price and the estimated notional; the hook approves only on the literal `True` — anything else is `declined` |

`runner.execute` is the only code that calls `broker.place` / `broker.cancel`, and only after
`validate_order` → `broker.preview` → the three walls. Defense in depth: `WebullBroker.place()` independently
raises `BrokerError` when `armed()` is false, so even a caller that bypassed the runner's gate still hits a
wall inside the adapter.

## Order of operations per symbol

0. **Exit already working** — holding and a non-stop SELL is already working (a re-run after an exit was
   placed): skip the symbol entirely, so a second full-position exit or a fresh stop is never stacked on it.
1. **Exit** — holding and the strategy says SELL: cancel the resting stop, then a MARKET SELL for the full
   position. The cancel is issued only for a SELL the gate already allowed, so a position is never stripped
   of its stop unless it is being sold. With more than one resting stop the exit is refused (`exit skipped`,
   "resolve manually") rather than cancelling one and leaving another working behind the SELL.
2. **Protect** — holding, no resting stop, the strategy carries a stop level: a STOP SELL GTC.
3. **Entry** — not holding, the strategy says BUY, a slot is free: a MARKET BUY sized
   `floor(dollars_per_position / last_price)`. A validated entry that a wall stopped (dry-run, refused,
   declined) still holds its slot for the rest of the cycle, so a preview never shows more entries than a
   confirmed run would make.

## Exit-path failure handling

The cancel-then-sell sequence is deliberately conservative about a position's protection (see the `submit` and
`restore_stop` helpers inside `runner.execute`):

- If `broker.cancel` **raises**, or answers a dict with `cancelled: False`, the resting stop is treated as
  still in place and the SELL is **not placed** — logged as `cancel not-cancelled` — so a position is never
  left with no exit AND no stop, and there is never a double exit.
- If the cancel succeeds but the SELL that follows then **raises, or comes back with `placed=False`**, the
  runner immediately re-places the just-cancelled stop (logged `protect restored`), so a failed exit never
  silently drops the position's protection.
- If that re-place **also** fails or raises, the runner logs `protect unprotected` and increments
  `CycleReport.unprotected` — the one failure mode the runner cannot self-heal — which surfaces in the
  `placed=... unprotected=...` summary line printed by `rails run` so it is never swallowed quietly.

## `PlaceResult.placed`

Every `Broker.place` returns a `PlaceResult`. The runner branches only on `.placed`. A broker that returned
its raw response body once made a runner treat an accepted order as a failure and abandon it; the seam test
`tests/test_broker_contract.py` pins every shipped broker to the contract.

## The paper broker never raises

`PaperBroker.preview` / `.place` never raise. An unpriceable symbol (no bar available, or a validation
failure) is rejected the ordinary way, `PlaceResult(placed=False, raw={"error": ...})`, not an exception. A BUY
whose cash need is only revealed at fill time — the reservation made at `place()` used a reference price, but
the bar it actually fills against can move the price — is rejected inside `sync()` and recorded in the paper
state's `rejected` list instead of ever taking cash negative.

## What validation rejects

Invalid side/type/time-in-force; empty symbol; non-positive or fractional quantity; a LIMIT without a limit or
carrying a stop; a MARKET carrying any price; a STOP without a stop or carrying a limit; a STOP_LIMIT missing
either; any price ≤ 0; a limit/stop farther than `max_price_deviation` from the last price; a BUY stop below /
SELL stop above the last price (it would fill immediately); a notional above `max_order_notional`.
