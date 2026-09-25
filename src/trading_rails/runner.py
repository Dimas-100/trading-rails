"""One cycle of the trading loop. The ONLY module that calls broker.place / broker.cancel, and it does so
only after: validate_order -> broker.preview -> gate_decision (should_submit(confirm) AND broker.armed())
-> optional ask() hook (can only decline). Order of operations per symbol: exits, protect, entries."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .broker import BarSource, Broker
from .config import RunConfig
from .fills import describe
from .models import Order, OrderType, Side, TimeInForce
from .safety import OrderValidationError, should_submit, validate_order
from .strategy import Strategy

AskFn = Callable[[Order, str], bool]


@dataclass
class CycleReport:
    as_of: str | None
    rows: list[dict] = field(default_factory=list)
    placed: int = 0
    dry_run: int = 0
    refused: int = 0
    declined: int = 0
    skipped: int = 0
    errors: int = 0
    unprotected: int = 0


def gate_decision(broker: Broker, confirm) -> str:
    """'submit' only when should_submit(confirm) AND broker.armed() is the literal True; 'dry-run' when
    not confirmed; 'refused' when confirmed but the broker is not armed (armed() must answer the literal
    True, not merely something truthy). Each wall only adds strictness."""
    if not should_submit(confirm):
        return "dry-run"
    if broker.armed() is not True:
        return "refused"
    return "submit"


def _is_stop(row: dict) -> bool:
    return row.get("side") == "SELL" and row.get("order_type") in ("STOP", "STOP_LIMIT")


def execute(config: RunConfig, strategy: Strategy, broker: Broker, source: BarSource, *,
            confirm=False, ask: AskFn | None = None, as_of: str | None = None,
            log_path: str | Path | None = "data/runs.jsonl", now: datetime | None = None) -> CycleReport:
    report = CycleReport(as_of=as_of)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    log_file = Path(log_path) if log_path else None

    def log(symbol: str, step: str, status: str, detail: str = "", order: Order | None = None, extra=None):
        row = {"ts": stamp, "as_of": as_of, "symbol": symbol, "step": step, "status": status, "detail": detail,
               "order": (asdict(order) | {"side": Side(order.side).value,
                                          "order_type": OrderType(order.order_type).value,
                                          "time_in_force": TimeInForce(order.time_in_force).value}) if order else None,
               "extra": extra}
        report.rows.append(row)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        return row

    def restore_stop(symbol: str, row: dict, last: float) -> None:
        """Re-place the stop that was just cancelled ahead of a SELL that then failed or raised, so a
        cancel-then-place failure never leaves a position silently unprotected."""
        restore = Order(symbol=symbol, side=Side.SELL, quantity=int(row["quantity"]), order_type=OrderType.STOP,
                        stop_price=float(row["stop_price"]), time_in_force=TimeInForce.GTC)
        try:
            validate_order(restore, last_price=last, max_price_deviation=config.max_price_deviation,
                           max_notional=config.max_order_notional)
        except OrderValidationError as exc:
            log(symbol, "protect", "invalid", str(exc), restore)
            report.unprotected += 1
            return
        try:
            rresult = broker.place(restore)
        except Exception as exc:
            log(symbol, "protect", "unprotected", f"{type(exc).__name__}: {exc}", restore)
            report.unprotected += 1
            return
        if rresult.placed:
            log(symbol, "protect", "restored", describe(restore), restore, rresult.raw)
        else:
            log(symbol, "protect", "unprotected", json.dumps(rresult.raw, default=str)[:500], restore)
            report.unprotected += 1

    def submit(symbol: str, step: str, order: Order, last: float, cancel_first: dict | None = None) -> bool:
        """validate -> preview -> gate -> ask -> (cancel) -> place. Returns True when placed.
        `cancel_first`, when given, is the WHOLE resting stop's open-order row (not just its id): a
        refused/raising cancel skips the SELL entirely (no double exit, logged 'not-cancelled'); a SELL
        that then fails or raises after a successful cancel triggers restore_stop so the stop comes back."""
        try:
            validate_order(order, last_price=last, max_price_deviation=config.max_price_deviation,
                           max_notional=config.max_order_notional)
        except OrderValidationError as exc:
            log(symbol, step, "invalid", str(exc), order)
            return False
        preview = broker.preview(order)
        log(symbol, step, "preview", describe(order), order, preview)
        decision = gate_decision(broker, confirm)
        if decision != "submit":
            log(symbol, step, decision, describe(order), order)
            if cancel_first:
                log(symbol, "cancel", decision, f"would cancel {cancel_first['client_order_id']}")
                report.dry_run += decision == "dry-run"
                report.refused += decision == "refused"
            report.dry_run += decision == "dry-run"
            report.refused += decision == "refused"
            return False
        if ask is not None and ask(order, describe(order)) is not True:
            log(symbol, step, "declined", describe(order), order)
            report.declined += 1
            return False

        if cancel_first:
            cid = cancel_first["client_order_id"]
            try:
                res = broker.cancel(cid)
            except Exception as exc:
                log(symbol, "cancel", "not-cancelled", f"{type(exc).__name__}: {exc}")
                report.skipped += 1
                return False
            if isinstance(res, dict) and not res.get("cancelled", True):
                log(symbol, "cancel", "not-cancelled", json.dumps(res, default=str)[:500])
                report.skipped += 1
                return False
            log(symbol, "cancel", "cancelled", cid, None, res)

        try:
            result = broker.place(order)
        except Exception as exc:
            log(symbol, step, "error", f"{type(exc).__name__}: {exc}", order)
            if cancel_first:
                restore_stop(symbol, cancel_first, last)
            return False
        if result.placed:
            report.placed += 1
            log(symbol, step, "placed", result.order_id or "", order, result.raw)
            return True
        log(symbol, step, "not-placed", json.dumps(result.raw, default=str)[:500], order, result.raw)
        if cancel_first:
            restore_stop(symbol, cancel_first, last)
        return False

    if hasattr(broker, "sync"):
        broker.sync(as_of)
    positions = {p.symbol: p for p in broker.positions() if p.quantity > 0}
    open_orders = broker.open_orders()
    working_buys = {r["symbol"] for r in open_orders if r.get("side") == "BUY"}
    slots_used = len(set(positions) | working_buys)

    for symbol in config.symbols:
        try:
            bars = source.bars(symbol, config.bars_lookback, as_of)
            if not bars:
                report.skipped += 1
                log(symbol, "bars", "skipped", "no bars")
                continue
            last = float(source.last_price(symbol, as_of))
            signal = strategy.on_bars(bars)
            log(symbol, "signal", "ok", signal.note,
                extra={"action": signal.action.value if signal.action else None,
                       "stop": signal.stop_price, "last": last})
            held = positions.get(symbol)
            resting = [r for r in open_orders if r.get("symbol") == symbol and _is_stop(r)]

            if held and signal.action == Side.SELL:
                order = Order(symbol=symbol, side=Side.SELL, quantity=held.quantity, order_type=OrderType.MARKET)
                submit(symbol, "exit", order, last, cancel_first=resting[0] if resting else None)
                continue

            if held and not resting and signal.stop_price is not None:
                order = Order(symbol=symbol, side=Side.SELL, quantity=held.quantity, order_type=OrderType.STOP,
                              stop_price=float(signal.stop_price), time_in_force=TimeInForce.GTC)
                submit(symbol, "protect", order, last)
                continue

            if not held and symbol not in working_buys and signal.action == Side.BUY:
                if slots_used >= config.max_positions:
                    report.skipped += 1
                    log(symbol, "entry", "skipped", f"no free slots ({slots_used}/{config.max_positions})")
                    continue
                qty = int(math.floor(config.dollars_per_position / last)) if last > 0 else 0
                if qty < 1:
                    report.skipped += 1
                    log(symbol, "entry", "skipped", f"quantity 0 at last {last}")
                    continue
                order = Order(symbol=symbol, side=Side.BUY, quantity=qty, order_type=OrderType.MARKET)
                if submit(symbol, "entry", order, last):
                    slots_used += 1
        except Exception as exc:  # a broker/data failure on one symbol must not abort the cycle
            report.errors += 1
            log(symbol, "cycle", "error", f"{type(exc).__name__}: {exc}")
    return report
