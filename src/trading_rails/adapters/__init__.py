"""Broker adapter registry. `paper` is built in; `webull` needs the optional extra."""
from __future__ import annotations

import importlib.util

from ..broker import Broker
from ..config import RunConfig


def _webull_installed() -> bool:
    """True only when the official SDK's `webull.core` resolves (an unrelated package named `webull` does
    not count). find_spec on a dotted name imports the parent, which raises when `webull` is absent."""
    try:
        return importlib.util.find_spec("webull.core") is not None
    except (ImportError, ValueError):
        return False


def available() -> dict[str, str]:
    return {"paper": "built-in",
            "webull": "installed" if _webull_installed() else "needs: pip install -e '.[webull]'"}


def get_broker(name: str, config: RunConfig) -> Broker:
    if name == "paper":
        from ..data import CsvBars, SyntheticBars
        from ..paper import PaperBroker
        src = CsvBars(config.data_source[4:]) if config.data_source.startswith("csv:") else SyntheticBars()
        return PaperBroker(src, config.paper_state_path, starting_cash=config.paper_starting_cash)
    if name == "webull":
        if not _webull_installed():
            raise ImportError("the Webull adapter needs the SDK: pip install -e '.[webull]'")
        from .webull import WebullBroker
        return WebullBroker.from_env()
    raise KeyError(f"unknown broker {name!r}; available: {sorted(available())}")
