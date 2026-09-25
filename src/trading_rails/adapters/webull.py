"""Webull OpenAPI adapter: implements Broker (execution + account) and BarSource (daily bars, last price).

Install with `pip install -e ".[webull]"`. Env: WEBULL_APP_KEY, WEBULL_APP_SECRET, WEBULL_REGION (us),
WEBULL_HOST (api.webull.com; a PaperTrade-portal key uses api.sandbox.webull.com), WEBULL_OPENAPI_TOKEN_DIR
(.webull-tokens — the SDK stores its 2FA token there; RELATIVE paths resolve against the CWD, so set an
absolute path when you run from more than one directory), RAILS_LIVE_ENABLED (1 = armed).

`from_env` loads a `.env` from the working directory (python-dotenv) before reading the variables above;
a `RAILS_LIVE_ENABLED=1` line there arms the adapter, so keep it out of `.env` unless you mean it.

Pure helpers (translation, parsing) sit at module level so they are unit-tested without the SDK; the SDK
is imported lazily in `from_env` so this module loads in the base install."""
from __future__ import annotations

import os
from pathlib import Path

from ..broker import OPEN_ORDER_KEYS, BrokerError
from ..data import sort_bars
from ..models import Balance, Bar, Order, OrderType, PlaceResult, Position, Side, TimeInForce

DEFAULT_HOST = "api.webull.com"
ENTITLEMENT_MESSAGE = ("Webull market data is not entitled for this app key. Claim the FREE 'Nasdaq Basic - "
                       "Non Display' tier under OpenAPI Advanced Quotes on the Webull developer site.")
_TO_WEBULL_TYPE = {"MARKET": "MARKET", "LIMIT": "LIMIT", "STOP": "STOP_LOSS", "STOP_LIMIT": "STOP_LOSS_LIMIT"}
_FROM_WEBULL_TYPE = {v: k for k, v in _TO_WEBULL_TYPE.items()}


# ── pure helpers ───────────────────────────────────────────────────────────────
def to_webull(order: Order) -> dict:
    d = {"client_order_id": order.client_order_id, "combo_type": "NORMAL", "symbol": order.symbol,
         "instrument_type": "EQUITY", "market": "US",
         "order_type": _TO_WEBULL_TYPE[OrderType(order.order_type).value], "quantity": str(int(order.quantity)),
         "support_trading_session": "CORE", "side": Side(order.side).value,
         "time_in_force": TimeInForce(order.time_in_force).value, "entrust_type": "QTY"}
    if order.limit_price is not None:
        d["limit_price"] = f"{float(order.limit_price):.2f}"
    if order.stop_price is not None:
        d["stop_price"] = f"{float(order.stop_price):.2f}"
    return d


def _num(d, keys, default=None):
    for k in keys:
        v = (d or {}).get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return default


def _str(d, keys):
    for k in keys:
        v = (d or {}).get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def bars_from_rows(raw) -> list[Bar]:
    rows = raw if isinstance(raw, list) else (raw.get("data") if isinstance(raw, dict) else []) or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        o, h, lo, c = (_num(r, ["open", "o"]), _num(r, ["high", "h"]), _num(r, ["low", "l"]), _num(r, ["close", "c"]))
        if None in (o, h, lo, c):
            continue
        out.append(Bar(ts=_str(r, ["time", "t", "timestamp"]), open=o, high=h, low=lo, close=c,
                       volume=_num(r, ["volume", "v"], 0.0)))
    return sort_bars(out)


def _leg(row: dict) -> dict:
    items = row.get("items") if isinstance(row, dict) else None
    leg = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    inst = row.get("instrument") if isinstance(row.get("instrument"), dict) else {}
    return {**row, **inst, **leg}


def position_from_row(row: dict) -> Position | None:
    r = _leg(row)
    symbol = _str(r, ["symbol", "ticker"]).upper()
    qty = _num(r, ["quantity", "qty", "position", "shares"])
    if not qty or qty <= 0:
        return None
    if not symbol:
        raise BrokerError(f"position row with quantity {qty} but no symbol: {row}")
    cost_keys = ["cost_price", "costPrice", "avgCost", "averageCost", "unitCost", "unit_cost", "cost"]
    return Position(symbol=symbol, quantity=int(qty), avg_cost=_num(r, cost_keys, 0.0),
                    last_price=_num(r, ["last_price", "lastPrice"]))


def balance_from_body(body: dict) -> Balance:
    assets = body.get("account_currency_assets") if isinstance(body, dict) else None
    first = assets[0] if isinstance(assets, list) and assets and isinstance(assets[0], dict) else {}
    merged = {**first, **{k: v for k, v in (body or {}).items() if k != "account_currency_assets"}}
    net = _num(merged, ["total_net_liquidation_value", "net_liquidation_value"], 0.0)
    cash = _num(merged, ["settled_cash", "total_cash_balance", "cash_balance"], 0.0)
    bp = _num(merged, ["buying_power", "total_buying_power"], cash)
    return Balance(cash=cash, buying_power=bp, net_liq=net)


def open_order_from_row(row: dict) -> dict | None:
    r = _leg(row)
    symbol = _str(r, ["symbol", "ticker"]).upper()
    cid = _str(r, ["client_order_id", "order_id"])
    if not symbol:
        if cid:
            raise BrokerError(f"open order row without a symbol: {row}")
        return None
    qty = _num(r, ["quantity", "qty"], 0.0)
    otype = _FROM_WEBULL_TYPE.get(_str(r, ["order_type"]), _str(r, ["order_type"]))
    out = {"client_order_id": _str(r, ["client_order_id"]), "symbol": symbol, "side": _str(r, ["side"]).upper(),
           "order_type": otype, "quantity": int(qty or 0), "limit_price": _num(r, ["limit_price"]),
           "stop_price": _num(r, ["stop_price"]), "status": _str(r, ["status", "order_status"])}
    return {k: out[k] for k in OPEN_ORDER_KEYS}


def _rows(body, what: str) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        return body["data"]
    raise BrokerError(f"unexpected {what} payload: {type(body).__name__}")


def pick_cash_account(rows) -> str:
    rows = rows if isinstance(rows, list) else []
    cash = next((a for a in rows if isinstance(a, dict) and a.get("account_class") == "INDIVIDUAL_CASH"), None)
    if not cash or not cash.get("account_id"):
        raise BrokerError("no INDIVIDUAL_CASH account in the account list (never falling back to accounts[0])")
    return str(cash["account_id"])


def is_armed(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get("RAILS_LIVE_ENABLED", "")).strip() == "1"


def token_dir(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return str(Path(environ.get("WEBULL_OPENAPI_TOKEN_DIR") or ".webull-tokens").resolve())


def _check(res):
    status = getattr(res, "status_code", None)
    if status != 200:
        raise BrokerError(f"webull error {status}: {getattr(res, 'text', '')}")
    return res.json()


def _entitlement_error(exc) -> bool:
    msg = str(getattr(exc, "error_msg", "") or exc).lower()
    return getattr(exc, "http_status", None) == 401 and ("subscribe" in msg or "insufficient permission" in msg)


# ── the adapter ────────────────────────────────────────────────────────────────
class WebullBroker:
    name = "webull"

    def __init__(self, trade_client, data_client, *, environ=None):
        self._trade, self._data = trade_client, data_client
        self._environ = os.environ if environ is None else environ
        self._account: str | None = None

    @classmethod
    def from_env(cls, environ=None) -> "WebullBroker":
        environ = os.environ if environ is None else environ
        try:
            from dotenv import load_dotenv
            load_dotenv(Path.cwd() / ".env")      # exactly the CWD's .env: never a parent directory's
        except ImportError:
            pass
        try:
            from webull.core.client import ApiClient
            from webull.core.http.initializer.client_initializer import ClientInitializer
            from webull.data.data_client import DataClient
            from webull.trade.trade_client import TradeClient
        except ImportError as exc:
            raise ImportError("the Webull adapter needs the SDK: pip install -e '.[webull]'") from exc
        key, secret = environ.get("WEBULL_APP_KEY", "").strip(), environ.get("WEBULL_APP_SECRET", "").strip()
        if not key or not secret:
            raise BrokerError("WEBULL_APP_KEY and WEBULL_APP_SECRET must be set (see .env.example)")
        region = environ.get("WEBULL_REGION", "us").strip().lower()
        host = environ.get("WEBULL_HOST", DEFAULT_HOST).strip()
        # SDK 2.0.10 probes GET /openapi/config on client init and can raise before the token flow.
        orig = getattr(ClientInitializer, "_check_token_enable_orig", None) or ClientInitializer._check_token_enable

        def safe_check(api_client):
            try:
                return orig(api_client)
            except Exception:
                return True
        ClientInitializer._check_token_enable_orig = staticmethod(orig)
        ClientInitializer._check_token_enable = staticmethod(safe_check)

        def api():
            c = ApiClient(key, secret, region)
            c.add_endpoint(region, host)
            c.set_token_dir(token_dir(environ))
            return c
        return cls(TradeClient(api()), DataClient(api()), environ=environ)

    # Broker
    def armed(self) -> bool:
        return is_armed(self._environ)

    def account_id(self) -> str:
        if self._account is None:
            self._account = pick_cash_account(_check(self._trade.account_v2.get_account_list()))
        return self._account

    def preview(self, order: Order) -> dict:
        body = _check(self._trade.order_v2.preview_order(self.account_id(), [to_webull(order)]))
        return body if isinstance(body, dict) else {"result": body}

    def place(self, order: Order) -> PlaceResult:
        if not self.armed():
            raise BrokerError("webull adapter is not armed (RAILS_LIVE_ENABLED=1)")
        body = _check(self._trade.order_v2.place_order(self.account_id(), [to_webull(order)]))
        oid = body.get("order_id") if isinstance(body, dict) else None
        return PlaceResult(placed=bool(oid), order_id=str(oid) if oid else None,
                           raw=body if isinstance(body, dict) else {"result": body})

    def cancel(self, client_order_id: str) -> dict:
        body = _check(self._trade.order_v2.cancel_order(self.account_id(), client_order_id))
        return body if isinstance(body, dict) else {"result": body}

    def open_orders(self) -> list[dict]:
        rows = _rows(_check(self._trade.order_v2.get_order_open(self.account_id())), "open-orders")
        return [o for o in (open_order_from_row(r) for r in rows if isinstance(r, dict)) if o is not None]

    def positions(self) -> list[Position]:
        rows = _rows(_check(self._trade.account_v2.get_account_position(self.account_id())), "positions")
        return [p for p in (position_from_row(r) for r in rows if isinstance(r, dict)) if p is not None]

    def balance(self) -> Balance:
        return balance_from_body(_check(self._trade.account_v2.get_account_balance(self.account_id())))

    # BarSource
    def _market(self, fn, *args, **kwargs):
        try:
            return _check(fn(*args, **kwargs))
        except BrokerError:
            raise
        except Exception as exc:
            if _entitlement_error(exc):
                raise BrokerError(ENTITLEMENT_MESSAGE) from exc
            raise BrokerError(f"webull market data: {type(exc).__name__}: {exc}") from exc

    def bars(self, symbol: str, n: int, as_of: str | None = None) -> list[Bar]:
        raw = self._market(self._data.market_data.get_history_bar, symbol.upper(), "US_STOCK", "D", count=str(int(n)))
        bars = bars_from_rows(raw)
        if as_of is not None:
            bars = [b for b in bars if b.ts <= as_of]
        return bars[-n:]

    def last_price(self, symbol: str, as_of: str | None = None) -> float:
        raw = self._market(self._data.market_data.get_snapshot, symbol.upper(), "US_STOCK")
        row = raw[0] if isinstance(raw, list) and raw else raw
        price = _num(row, ["price", "last_price", "close"])
        if price is None:
            raise BrokerError(f"no price in snapshot for {symbol}: {row}")
        return price
