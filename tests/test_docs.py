"""The README quickstart must name commands that exist, and no doc may leak an owner-specific value."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_quickstart_commands_exist():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for cmd in ("pip install -e \".[dev]\"", "pytest", "rails backtest", "rails run --config rails.toml --paper",
                "rails.example.toml"):
        assert cmd in text, cmd
    assert "RAILS_LIVE_ENABLED" in text and "CONFIRM" in text
    assert "not investment advice" in text.lower()


def test_no_owner_specific_values_in_tracked_text():
    # The public GitHub handle is allowed (badge/clone URLs; the LICENSE names the owner);
    # private machine paths and internal hosts are not.
    bad = re.compile(r"(junio|C:\\Users|project-warehouse|webullbroker\.com)", re.I)
    paths = (list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md"))
             + [ROOT / ".env.example", ROOT / "rails.example.toml"])
    for p in paths:
        assert not bad.search(p.read_text(encoding="utf-8")), p


def test_env_example_has_only_placeholders():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            assert value.strip() in ("", "0", "us", "api.webull.com", ".webull-tokens") or "your_" in value, line
