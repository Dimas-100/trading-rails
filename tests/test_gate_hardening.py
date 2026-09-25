"""AST guards over the whole package. Only runner.py calls `.place(...)` / `.cancel(...)` (and there only inside
its gated `submit` and the `restore_stop` it calls after a gated SELL failed); nothing in src/ passes a literal
`confirm=True` or defaults a `confirm` parameter to True. Method DEFINITIONS elsewhere are fine: only calls count,
and the SDK's `place_order` / `cancel_order` are different names."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "trading_rails"
GUARDED = ("place", "cancel")


def _modules():
    files = sorted(SRC.rglob("*.py"))
    assert files and any(f.name == "runner.py" for f in files)
    return [(f, ast.parse(f.read_text(encoding="utf-8"), filename=str(f))) for f in files]


def _guarded_calls(tree):
    """(call, name of the innermost enclosing function) for every `<x>.place(...)` / `<x>.cancel(...)` call."""
    out = []

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr in GUARDED):
                out.append((child, fn))
            visit(child, inner)
    visit(tree, None)
    return out


def test_only_runner_calls_place_or_cancel():
    offenders = []
    for path, tree in _modules():
        if path.name == "runner.py" and path.parent == SRC:
            continue
        offenders += [f"{path.relative_to(SRC)}:{call.lineno} .{call.func.attr}()" for call, _ in _guarded_calls(tree)]
    assert offenders == [], f"only runner.py may call broker.place/cancel: {offenders}"


def test_runner_calls_them_only_inside_submit_and_restore_stop():
    tree = ast.parse((SRC / "runner.py").read_text(encoding="utf-8"))
    calls = _guarded_calls(tree)
    assert {c.func.attr for c, _ in calls} == {"place", "cancel"}           # the walker is not vacuous
    assert {fn for _, fn in calls} <= {"submit", "restore_stop"}, calls


def test_no_literal_confirm_true_anywhere_in_src():
    hits = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "confirm" and \
                    isinstance(node.value, ast.Constant) and node.value.value is True:
                hits.append(f"{path.relative_to(SRC)}:{node.value.lineno} confirm=True")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                pos = a.posonlyargs + a.args
                pairs = list(zip(pos[len(pos) - len(a.defaults):], a.defaults))
                pairs += list(zip(a.kwonlyargs, a.kw_defaults))
                for arg, default in pairs:
                    if arg.arg == "confirm" and isinstance(default, ast.Constant) and default.value is True:
                        hits.append(f"{path.relative_to(SRC)}:{node.lineno} {node.name}(confirm=True default)")
    assert hits == [], hits
