# Adding a strategy

A strategy is any class with `name`, `warmup() -> int` and `on_bars(bars) -> Signal`, decorated with
`@trading_rails.strategy.register` and imported from `strategies/__init__.py`.

- `bars` arrive ascending (oldest first), at most `bars_lookback` of them (config), the same in backtest and live.
- Return `Signal(Side.BUY, stop, note)` only on the bar that triggers an entry, `Signal(Side.SELL, None, note)`
  only on the bar that triggers an exit, and `Signal(None, stop_or_None, note)` otherwise.
- Carry `stop_price` whenever you want a protective stop under a held position; the runner places it once
  (when none rests) and the backtest sets it from the entry bar's signal.
- Return `Signal(None, None, "warmup")` when `len(bars) < warmup()`.
- Indicators in `indicators.py` (`sma`, `ema`, `rsi`, `atr`) are pure lists aligned to the input.

```python
from trading_rails.indicators import rsi
from trading_rails.models import Side, Signal
from trading_rails.strategy import register

@register
class Rsi2:
    name = "rsi2"
    def __init__(self, entry=10.0, exit=65.0): self.entry, self.exit = entry, exit
    def warmup(self): return 5
    def on_bars(self, bars):
        if len(bars) < self.warmup(): return Signal(None, None, "warmup")
        r = rsi([b.close for b in bars], 2)[-1]
        if r is None: return Signal(None, None, "warmup")
        if r < self.entry: return Signal(Side.BUY, None, f"rsi2 {r:.1f}")
        if r > self.exit: return Signal(Side.SELL, None, f"rsi2 {r:.1f}")
        return Signal(None, None, f"rsi2 {r:.1f}")
```

Then `rails backtest --strategy rsi2 --source synthetic`.
