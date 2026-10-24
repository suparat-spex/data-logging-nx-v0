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
