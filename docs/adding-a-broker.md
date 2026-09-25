# Adding a broker

1. Copy `src/trading_rails/adapters/template.py` to `src/trading_rails/adapters/<name>.py`.
2. Implement the `Broker` methods (`armed`, `account_id`, `preview`, `place`, `cancel`, `open_orders`,
   `positions`, `balance`) and, if the broker serves data, the `BarSource` methods (`bars`, `last_price`). A
   broker that does not implement `BarSource` can still be used dry-run and can back a paper run, but
   `rails run --broker NAME --live` refuses it outright — a live run prices only from the broker itself, never
   from `--as-of` or a separately-configured data source.
3. Put the neutral-`Order` → wire-format translation in one pure function and unit-test it without the network
   (see `to_webull` and `tests/test_adapters_webull.py`).
4. `place` must return `PlaceResult(placed=<accepted?>, order_id, raw)`. Add your broker to
   `tests/test_broker_contract.py`.
5. `armed()` must read an explicit opt-in (convention: `RAILS_LIVE_ENABLED=1`) and default to False. `place()`
   itself must also refuse (raise `BrokerError`) when `armed()` is false — never rely solely on the runner's
   gate to keep an unarmed adapter from submitting.
6. `bars()` must return ascending bars — pass them through `data.sort_bars`.
7. Register the name in `adapters/__init__.py` (`available()` and `get_broker()`); if the SDK is optional, add
   an extra in `pyproject.toml` and guard the import.
8. Raise `BrokerError` on failure; the runner logs it and continues with the next symbol.
9. Parse account/order payloads fail-closed: an unrecognised or malformed response must raise `BrokerError`,
   never quietly return an empty list. An empty `positions()`/`open_orders()` caused by a parse failure reads
   as "flat" to the runner, and a position that is actually held can then lose its stop or get bought again.
   See `webull.py`'s `_rows`, `position_from_row` and `open_order_from_row`: raise on a shape the parser
   doesn't recognise, and only skip (return `None`) a row that is recognised but genuinely empty.
