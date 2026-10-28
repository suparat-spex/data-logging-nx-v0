"""
channel_math.py - channel scaling for NX Logger: linear, two-point, safe formulas.

Scaling turns the value the device sends (x, already in the packet's units) into
the engineering value shown, plotted and logged (CSV / TDMS). Raw .wpk files keep
the original packets; the scaling used is saved in the session's project.json.

  none        y = x
  linear      y = m*x + b                (Tare sets b so the current value reads 0)
  two-point   y through (x1, y1) and (x2, y2)
  formula     any expression in x, e.g.  x*9/5 + 32   sqrt(abs(x))   where(x > 0, x, 0)

Formulas are NOT Python scripts: they are parsed and only numbers, x, + - * / ** %,
comparisons and the whitelisted numpy functions below are allowed. A project file
from someone else therefore cannot run code on your computer.
"""
from __future__ import annotations

import ast
import re

import numpy as np


FUNCS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "arcsin": np.arcsin, "arccos": np.arccos,
    "arctan": np.arctan, "sqrt": np.sqrt, "abs": np.abs, "exp": np.exp, "log": np.log,
    "log10": np.log10, "floor": np.floor, "ceil": np.ceil, "round": np.round, "sign": np.sign,
    "clip": np.clip, "minimum": np.minimum, "maximum": np.maximum, "where": np.where,
    "degrees": np.degrees, "radians": np.radians,
}
CONSTS = {"pi": np.pi, "e": np.e}
_OK_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant,
             ast.Compare, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
             ast.USub, ast.UAdd, ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq,
             ast.BitAnd, ast.BitOr)
UNITS = ["", "V", "mV", "A", "mA", "°C", "°F", "K", "bar", "kPa", "Pa", "psi", "g", "m/s²",
         "mm", "m", "N", "kN", "N·m", "rpm", "Hz", "%", "µε", "Ω", "W", "kW"]
SCALE_TYPES = [("none", "None (as received)"), ("linear", "Linear  y = m·x + b"),
               ("twopoint", "Two-point"), ("formula", "Formula of x")]


def identifier(text: str) -> str:
    """A formula-safe name from free text: 'Simulator · CH 1' -> 'Simulator_CH_1'."""
    s = "".join(c if c.isalnum() else "_" for c in text.strip())
    s = "_".join(p for p in s.split("_") if p) or "v"
    return s if not s[0].isdigit() else "_" + s


def formula_names(expr: str) -> list[str]:
    """Names a formula reads (not functions / constants), in order of appearance."""
    try:
        tree = ast.parse((expr or "").strip(), mode="eval")
    except SyntaxError:
        return []
    out = []
    nodes = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Name)),
                   key=lambda n: (n.lineno, n.col_offset))
    for node in nodes:
        if node.id not in FUNCS and node.id not in CONSTS and node.id not in out:
            out.append(node.id)
    return out


def compile_expr(expr: str, names, example: str = "x * 9/5 + 32"):
    """
    Validate and compile a formula over the given variable names.
    Returns f(env: {name: ndarray | float}) -> ndarray (broadcast to the inputs), or
    raises ValueError with a message for the user.
    """
    expr, names = (expr or "").strip(), set(names)
    if not expr:
        raise ValueError(f"Enter a formula, e.g.  {example}")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Syntax error: {e.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _OK_NODES):
            raise ValueError(f"Not allowed in a formula: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in names and node.id not in FUNCS \
                and node.id not in CONSTS:
            hint = ", ".join(sorted(names)[:6]) + (" ..." if len(names) > 6 else "")
            raise ValueError(f"Unknown name '{node.id}' (use {hint}, {', '.join(sorted(CONSTS))} "
                             "or a function)")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in FUNCS):
            raise ValueError("Only the listed functions can be called")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("Only numbers are allowed as constants")
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            raise ValueError("Write 0 < x < 1 as (x > 0) & (x < 1)")
    code = compile(tree, "<formula>", "eval")
    env = {"__builtins__": {}, **FUNCS, **CONSTS}

    def f(values: dict):
        loc = {k: np.asarray(v, dtype=float) for k, v in values.items()}
        with np.errstate(all="ignore"):
            r = eval(code, env, loc)               # noqa: S307 - AST checked above
        shape = np.broadcast_shapes(*(a.shape for a in loc.values())) if loc else ()
        return np.broadcast_to(np.asarray(r, dtype=float), shape).copy()

    try:
        f({n: np.array([0.0, 1.0]) for n in names})  # catches wrong argument counts etc.
    except Exception as e:
        raise ValueError(f"Formula error: {e}") from None
    return f


def compile_formula(expr: str):
    """Validate and compile a formula of x. Returns f(x: ndarray) -> ndarray, or raises ValueError."""
    g = compile_expr(expr, {"x"})
    return lambda x: g({"x": x})


# ====================================================================== scalar formulas (Virtual tags)
# One value per evaluation (number, text or boolean), not arrays. The tree is walked by a small
# interpreter (no eval): only the nodes / functions below exist, so project files stay safe.
class ScalarError(ValueError):
    """A formula that cannot be evaluated with these values (shown as the tag's error)."""


def as_number(x):
    if isinstance(x, str):
        try:
            return float(x.strip())
        except ValueError:
            raise ScalarError(f"num('{x}'): not a number") from None
    return float(x)


def as_text(x, dec=None):
    if isinstance(x, str):
        return x
    if dec is not None:
        return f"{float(x):.{int(dec)}f}"
    return f"{float(x):g}"


SCALAR_FUNCS = {   # name: (function, help)
    "sqrt": (lambda x: as_number(x) ** 0.5, "square root"), "abs": (lambda x: abs(as_number(x)), "absolute value"),
    "min": (lambda *a: min(as_number(x) for x in a), "smallest of the values"),
    "max": (lambda *a: max(as_number(x) for x in a), "largest of the values"),
    "avg": (lambda *a: sum(as_number(x) for x in a) / len(a), "average of the values"),
    "clip": (lambda x, lo, hi: min(max(as_number(x), as_number(lo)), as_number(hi)), "clip(x, lo, hi)"),
    "round": (lambda x, n=0: round(as_number(x), int(n)), "round(x, decimals)"),
    "floor": (lambda x: float(np.floor(as_number(x))), "round down"), "ceil": (lambda x: float(np.ceil(as_number(x))), "round up"),
    "exp": (lambda x: float(np.exp(as_number(x))), "e^x"), "log": (lambda x: float(np.log(as_number(x))), "natural log"),
    "log10": (lambda x: float(np.log10(as_number(x))), "log base 10"),
    "sin": (lambda x: float(np.sin(as_number(x))), "sine (radians)"), "cos": (lambda x: float(np.cos(as_number(x))), "cosine"),
    "tan": (lambda x: float(np.tan(as_number(x))), "tangent"),
    "bit": (lambda x, n: float((int(as_number(x)) >> int(as_number(n))) & 1), "bit(x, n): bit n of a word, 0 / 1"),
    "num": (as_number, "text -> number, e.g. num(\"12.5\")"), "text": (as_text, "number -> text, text(x, decimals)"),
}
SCALAR_IF = "_if_"         # users write if(condition, then, else) - `if` is a Python keyword, so _prep renames it
_STR = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")      # "text" / 'text' literals
_IF = re.compile(r"\bif(\s*\()")


def _prep(expr: str) -> str:
    """if( -> _if_( outside "text" literals (only the chosen branch of an if runs)."""
    parts = _STR.split((expr or "").strip())
    return "".join(x if i % 2 else _IF.sub(SCALAR_IF + r"\1", x) for i, x in enumerate(parts))


_SC_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Name, ast.Load,
             ast.Constant, ast.Tuple, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
             ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq,
             ast.In, ast.NotIn)
_SC_CONSTS = {"pi": np.pi, "e": np.e, "true": True, "false": False, "True": True, "False": False}


def scalar_names(expr: str) -> list[str]:
    """Names a scalar formula reads (not functions / constants), in order of appearance."""
    try:
        tree = ast.parse(_prep(expr), mode="eval")
    except SyntaxError:
        return []
    out = []
    for node in sorted((n for n in ast.walk(tree) if isinstance(n, ast.Name)), key=lambda n: (n.lineno, n.col_offset)):
        if node.id not in SCALAR_FUNCS and node.id != SCALAR_IF and node.id not in _SC_CONSTS and node.id not in out:
            out.append(node.id)
    return out


def _kind(x) -> str:
    return "text" if isinstance(x, str) else "number"


def _arith(op, a, b):
    if isinstance(a, str) or isinstance(b, str):
        if isinstance(op, ast.Add) and isinstance(a, str) and isinstance(b, str):
            return a + b                                   # "Line " + text(N)
        raise ScalarError("Text cannot be used in arithmetic (use num(...) or text(...))")
    a, b = float(a), float(b)
    if isinstance(op, ast.Add):
        return a + b
    if isinstance(op, ast.Sub):
        return a - b
    if isinstance(op, ast.Mult):
        return a * b
    if isinstance(op, ast.Pow):
        return a ** b
    if b == 0:
        raise ScalarError("Division by zero")
    if isinstance(op, ast.Div):
        return a / b
    return a % b if isinstance(op, ast.Mod) else a // b


def _cmp(op, a, b) -> bool:
    if isinstance(op, (ast.In, ast.NotIn)):
        seq = b if isinstance(b, tuple) else (b,)
        if any(_kind(a) != _kind(x) for x in seq):
            raise ScalarError("Cannot compare a number with a text (use num(...) or text(...))")
        return (a in seq) == isinstance(op, ast.In)
    if _kind(a) != _kind(b):
        raise ScalarError("Cannot compare a number with a text (use num(...) or text(...))")
    if isinstance(op, ast.Eq):
        return a == b
    if isinstance(op, ast.NotEq):
        return a != b
    if isinstance(a, str) and not isinstance(op, (ast.Eq, ast.NotEq)):
        raise ScalarError("Texts can only be compared with == / != / in")
    return {ast.Gt: a > b, ast.Lt: a < b, ast.GtE: a >= b, ast.LtE: a <= b}[type(op)]


def truthy(x) -> bool:
    return bool(x) if not isinstance(x, str) else x != ""


def compile_scalar(expr: str, names, example: str = "(A + B) * 3.6"):
    """
    Validate a scalar formula (Virtual tags) over the given names. Numbers, "text", true / false,
    + - * / ** % //, comparisons (also chained and `in (1, 2)`), and / or / not, if(c, a, b) and
    SCALAR_FUNCS. Returns f(env: {name: float | str | bool}) -> float | str | bool; raises
    ValueError with a message for the user. At run time a ScalarError says what went wrong.
    """
    expr, names = (expr or "").strip(), set(names)
    if not expr:
        raise ValueError(f"Enter a formula, e.g.  {example}")
    try:
        tree = ast.parse(_prep(expr), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Syntax error: {e.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _SC_NODES):
            raise ValueError(f"Not allowed in a formula: {type(node).__name__}")
        if isinstance(node, ast.Call):
            fn = node.func.id if isinstance(node.func, ast.Name) else None
            if fn != SCALAR_IF and fn not in SCALAR_FUNCS:
                raise ValueError(f"Unknown function{f' {fn}' if fn else ''} (use {', '.join(sorted(SCALAR_FUNCS))}, if)")
            if node.keywords:
                raise ValueError("Write function arguments without names")
            if fn == SCALAR_IF and len(node.args) != 3:
                raise ValueError("if needs three parts: if(condition, then, else)")
        elif isinstance(node, ast.Name) and node.id not in names and node.id not in _SC_CONSTS \
                and node.id not in SCALAR_FUNCS and node.id != SCALAR_IF:
            hint = ", ".join(sorted(names)[:6]) + (" ..." if len(names) > 6 else "")
            raise ValueError(f"Unknown name '{node.id}'" + (f" (use {hint})" if hint else ""))
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str, bool)):
            raise ValueError("Only numbers and \"text\" are allowed as constants")
    calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and id(node) not in calls and (node.id in SCALAR_FUNCS or node.id == SCALAR_IF):
            fn = "if" if node.id == SCALAR_IF else node.id
            raise ValueError(f"'{fn}' is a function: write {fn}(...)")

    def ev(n, env):
        if isinstance(n, ast.Expression):
            return ev(n.body, env)
        if isinstance(n, ast.Constant):
            return n.value if isinstance(n.value, str) else (bool(n.value) if isinstance(n.value, bool)
                                                              else float(n.value))
        if isinstance(n, ast.Name):
            if n.id in _SC_CONSTS:
                return _SC_CONSTS[n.id]
            return env[n.id]
        if isinstance(n, ast.Tuple):
            return tuple(ev(x, env) for x in n.elts)
        if isinstance(n, ast.BinOp):
            return _arith(n.op, ev(n.left, env), ev(n.right, env))
        if isinstance(n, ast.UnaryOp):
            x = ev(n.operand, env)
            if isinstance(n.op, ast.Not):
                return not truthy(x)
            if isinstance(x, str):
                raise ScalarError("Text cannot be used in arithmetic")
            return -float(x) if isinstance(n.op, ast.USub) else float(x)
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                return all(truthy(ev(x, env)) for x in n.values)
            return any(truthy(ev(x, env)) for x in n.values)
        if isinstance(n, ast.Compare):
            a = ev(n.left, env)
            for op, right in zip(n.ops, n.comparators):
                b = ev(right, env)
                if not _cmp(op, a, b):
                    return False
                a = b
            return True
        if isinstance(n, ast.Call):
            if n.func.id == SCALAR_IF:
                return ev(n.args[1], env) if truthy(ev(n.args[0], env)) else ev(n.args[2], env)
            try:
                return SCALAR_FUNCS[n.func.id][0](*(ev(x, env) for x in n.args))
            except ScalarError:
                raise
            except (TypeError, ValueError, OverflowError) as e:
                raise ScalarError(f"{n.func.id}(): {e}") from None
        raise ScalarError(f"Not allowed: {type(n).__name__}")

    def f(env: dict):
        with np.errstate(all="ignore"):
            r = ev(tree, env)
        if isinstance(r, tuple):
            raise ScalarError("The result is a list - use it only after `in`")
        return r
    return f


def linear_from_two_points(x1, y1, x2, y2):
    if x2 == x1:
        raise ValueError("x1 and x2 must differ")
    m = (y2 - y1) / (x2 - x1)
    return m, y1 - m * x1


def make_scaler(scale: dict | None):
    """scale dict -> f(x) or None (no scaling). Raises ValueError for a bad formula."""
    s = scale or {}
    kind = s.get("type", "none")
    if kind == "linear":
        m, b = float(s.get("m", 1.0)), float(s.get("b", 0.0))
        return None if (m, b) == (1.0, 0.0) else (lambda x: m * x + b)
    if kind == "twopoint":
        m, b = linear_from_two_points(float(s.get("x1", 0)), float(s.get("y1", 0)),
                                      float(s.get("x2", 1)), float(s.get("y2", 1)))
        return lambda x: m * x + b
    if kind == "formula":
        return compile_formula(s.get("expr", ""))
    return None


def describe_scale(scale: dict | None) -> str:
    s = scale or {}
    kind = s.get("type", "none")
    if kind == "linear":
        return f"y = {s.get('m', 1):g}·x {'+' if s.get('b', 0) >= 0 else '-'} {abs(s.get('b', 0)):g}"
    if kind == "twopoint":
        return f"2-pt ({s.get('x1', 0):g}→{s.get('y1', 0):g}, {s.get('x2', 1):g}→{s.get('y2', 1):g})"
    if kind == "formula":
        return f"y = {s.get('expr', '')}"
    return ""
