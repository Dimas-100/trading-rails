# Contributing

- Tests first; every change ships with a test. `pytest` and `ruff check .` must be clean.
- The submit gate is off-limits (see `CLAUDE.md` and `docs/safety-model.md`). PRs that touch `safety.py`,
  `runner.py` or `cli.py` need to explain how the three walls are preserved.
- New brokers: `docs/adding-a-broker.md`. New strategies: `docs/adding-a-strategy.md`.
- Never commit `.env`, tokens, or price data.
