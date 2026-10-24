"""
variables.py - calculated channels ("Variables") for NX Logger. No Qt.

A variable behaves like one more channel: it can be plotted, shown in numeric /
gauge / statistics tiles, alarmed and logged. Its key is ("var:<id>", 0).

Kinds
  formula     sample-by-sample math on channels and other variables, e.g.
                Simulator_CH1 - Simulator_CH2          (difference)
                sqrt(Simulator_CH1**2 + Simulator_CH2**2)   (vector sum)
              Names are the channel identifiers (topic_channel) and variable names.
              Arrays come from the block that triggered the calculation; a channel
              of another topic enters as its latest value (a scalar).
  statistic   one value per block from a source over a time window (mean, RMS,
              SD, crest factor, kurtosis, dominant frequency ...; see analysis.STATS)
  process     signal processing of one source, with state across blocks:
              integrate (acceleration -> velocity, with a drift high-pass),
              differentiate, moving average, moving RMS, low-pass, high-pass

Every variable has a "base" topic: the real topic whose blocks drive it. A formula
waveform has the base topic's sample rate; a statistic gives one point per block.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

import numpy as np

from nxlogger.core import analysis as an
from nxlogger.core import channel_math as cm


KINDS = {"formula": "Formula", "statistic": "Statistic", "process": "Signal processing"}
OPS = {   # key: (label, parameter label, default, unit)
    "integrate": ("Integrate  ∫x dt", "Drift high-pass", 1.0, "Hz"),
    "differentiate": ("Differentiate  dx/dt", "", 0.0, ""),
    "movavg": ("Moving average", "Window", 100, "samples"),
    "movrms": ("Moving RMS", "Window", 100, "samples"),
    "lowpass": ("Low-pass (1st order)", "Cut-off", 100.0, "Hz"),
    "highpass": ("High-pass (1st order)", "Cut-off", 1.0, "Hz"),
}
TEMPLATES = [   # quick signal operations: (label, formula with A / B placeholders)
    ("A + B", "A + B"), ("A − B", "A - B"), ("A × B", "A * B"), ("A ÷ B", "A / B"),
    ("|A|", "abs(A)"), ("A²", "A**2"), ("√(A² + B²)", "sqrt(A**2 + B**2)"),
    ("(A + B) / 2", "(A + B) / 2"), ("Gain · offset", "A * 1.0 + 0.0"),
    ("Clip 0..100", "clip(A, 0, 100)"),
]
VAR_PREFIX = "var:"


def var_key(vid: str) -> tuple[str, int]:
    return (VAR_PREFIX + vid, 0)


def is_var(tid: str) -> bool:
    return isinstance(tid, str) and tid.startswith(VAR_PREFIX)


def new_variable(kind: str = "formula", name: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name or "Var1", "enabled": True, "kind": kind,
            "unit": "", "color": "", "description": "", "log": True, "decimals": 3,
            "expr": "", "source": None, "stat": "rms", "window_s": 1.0,
            "op": "integrate", "param": OPS["integrate"][2]}


def normalize_variable(v: dict) -> dict:
    for k, d in new_variable(v.get("kind", "formula")).items():
        v.setdefault(k, d)
    return v


def describe(v: dict, label_of=lambda key: str(key)) -> str:
    """Short definition text for tables."""
    k = v.get("kind")
    if k == "formula":
        return f"= {v.get('expr', '')}"
    src = label_of(tuple(v["source"])) if v.get("source") else "(no source)"
    if k == "statistic":
        win = float(v.get("window_s") or 0)
        return f"{an.STAT_LABEL.get(v.get('stat'), v.get('stat'))} of {src}" + \
            (f" over {win:g} s" if win else " per block")
    op = OPS.get(v.get("op"), ("?", "", 0, ""))
    return f"{op[0]} of {src}" + (f"  ({op[1].lower()} {float(v.get('param') or 0):g} {op[3]})" if op[1] else "")


# ====================================================================== stateful operations
def iir1(x: np.ndarray, a: float, b: float, y0: float) -> np.ndarray:
    """y[n] = a·y[n-1] + b·x[n], vectorised in chunks (a^-k stays finite)."""
    x = np.asarray(x, dtype=float)
    if a <= 0:
        return b * x
    k = max(1, int(250 * math.log(10) / max(-math.log(a), 1e-12)))
    out = np.empty_like(x)
    for i in range(0, x.size, k):
        seg = x[i:i + k]
        p = a ** np.arange(1, seg.size + 1)
        out[i:i + k] = p * (y0 + b * np.cumsum(seg / p))
        y0 = out[i + seg.size - 1]
    return out


class Processor:
    """One `process` variable: keeps its state between blocks."""

    def __init__(self, op: str, param: float):
        self.op, self.param = op, float(param or 0)
        self.reset()

    def reset(self):
        self.prev = None            # last input sample
        self.acc = 0.0              # integrator / filter state
        self.hp = 0.0               # drift high-pass state (low-pass of the integral)
        self.tail = np.zeros(0)     # moving-window history

    def _lp(self, x, fc, dt, state):
        a = math.exp(-2 * math.pi * fc * dt) if fc > 0 else 0.0
        y = iir1(x, a, 1 - a, state)
        return y, float(y[-1]) if y.size else state

    def __call__(self, x: np.ndarray, dt: float) -> np.ndarray:
        x = np.where(np.isfinite(x), x, 0.0)
        op = self.op
        if op == "integrate":
            inc = (np.concatenate(([x[0] if self.prev is None else self.prev], x[:-1])) + x) / 2 * dt
            if self.prev is None:
                inc[0] = 0.0                         # the integral starts at 0 on the first sample
            y = self.acc + np.cumsum(inc)
            self.acc, self.prev = float(y[-1]), float(x[-1])
            if self.param > 0:                       # remove the drift of the integral
                lp, self.hp = self._lp(y, self.param, dt, self.hp)
                y = y - lp
            return y
        if op == "differentiate":
            prev = x[0] if self.prev is None else self.prev
            self.prev = float(x[-1])
            return np.diff(np.concatenate(([prev], x))) / dt
        if op in ("movavg", "movrms"):
            n = max(1, int(self.param))
            v = x * x if op == "movrms" else x
            buf = np.concatenate((self.tail, v))
            c = np.cumsum(np.concatenate(([0.0], buf)))
            k = np.arange(len(self.tail), buf.size)          # output positions
            lo = np.maximum(k + 1 - n, 0)
            y = (c[k + 1] - c[lo]) / (k + 1 - lo)
            self.tail = buf[-(n - 1):] if n > 1 else np.zeros(0)
            return np.sqrt(y) if op == "movrms" else y
        if op in ("lowpass", "highpass"):
            if self.prev is None:
                self.acc = float(x[0])               # start settled, no step at the start
            self.prev = float(x[-1])
            lp, self.acc = self._lp(x, self.param, dt, self.acc)
            return lp if op == "lowpass" else x - lp
        return x


# ====================================================================== engine
@dataclass
class VarBlock:
    """Looks like wavepacket.WaveBlock to TopicStream / recorder / alarms."""
    seq: int
    sample_idx: int
    t0: float
    dt: float
    y: np.ndarray                                    # shape (C, N)
    channels: list[int] = field(default_factory=lambda: [0])

    def __post_init__(self):
        self.scales = np.ones(len(self.channels), dtype=np.float32)

    @property
    def n(self) -> int:
        return self.y.shape[1]


class VariableEngine:
    """
    Compiles the variable definitions and computes them for each scaled block.
    Host callbacks (set by the window):
      channel_ids()            -> {identifier: (topic_id, index)} of all channels
      history(key, window_s)   -> (t, y) newest samples of a channel / variable, or None
      latest(key)              -> last value of a channel / variable, or None
    """

    def __init__(self):
        self.vars: list[dict] = []
        self.errors: dict[str, str] = {}             # var id -> message (shown in the table)
        self.base: dict[str, str] = {}               # var id -> base topic id
        self.order: list[dict] = []                  # dependency order
        self._fn: dict[str, object] = {}
        self._refs: dict[str, dict[str, tuple]] = {}  # formula var id -> {name: key} it reads
        self._proc: dict[str, Processor] = {}
        self._count: dict[str, int] = {}
        self.values: dict[str, float] = {}           # var id -> latest value
        self.channel_ids = lambda: {}
        self.history = lambda key, win: None
        self.latest = lambda key: None

    # ---- configuration
    def set_variables(self, variables: list[dict]):
        self.vars = variables
        for v in variables:
            normalize_variable(v)
        self._compile()

    def var_ids(self) -> dict[str, dict]:
        return {cm.identifier(v["name"]): v for v in self.vars}

    def names(self, exclude: str | None = None) -> dict[str, tuple]:
        """{identifier: key} usable in formulas (channels, then variables)."""
        out = dict(self.channel_ids())
        for v in self.vars:
            if v["id"] != exclude:
                out[cm.identifier(v["name"])] = var_key(v["id"])
        return out

    def check(self, v: dict) -> str:
        """'' if the definition is valid, else the message."""
        if not v.get("name", "").strip():
            return "Enter a name."
        ident = cm.identifier(v["name"])
        if ident in cm.FUNCS or ident in cm.CONSTS:
            return f"'{v['name']}' is a function name - choose another name."
        if ident in self.channel_ids():
            return f"'{ident}' is already a channel identifier - choose another name."
        if any(cm.identifier(o["name"]) == ident for o in self.vars if o["id"] != v["id"]):
            return f"Another variable is already called '{v['name']}'."
        k = v.get("kind")
        if k == "formula":
            try:
                cm.compile_expr(v.get("expr", ""), self.names(v["id"]), "Simulator_CH1 - Simulator_CH2")
            except ValueError as e:
                return str(e)
            if not any(n in self.names(v["id"]) for n in cm.formula_names(v.get("expr", ""))):
                return "The formula must use at least one channel or variable."
        elif not v.get("source"):
            return "Choose a source channel."
        elif tuple(v["source"]) == var_key(v["id"]):
            return "A variable cannot use itself as source."
        return ""

    def _deps(self, v: dict) -> list[tuple]:
        if v["kind"] == "formula":
            names = self.names(v["id"])
            return [names[n] for n in cm.formula_names(v.get("expr", "")) if n in names]
        return [tuple(v["source"])] if v.get("source") else []

    def _compile(self):
        self.errors.clear(); self._fn.clear(); self._refs.clear(); self.base.clear()
        by_key = {var_key(v["id"]): v for v in self.vars}
        live = [v for v in self.vars if v["enabled"]]
        for v in live:
            msg = self.check(v)
            if msg:
                self.errors[v["id"]] = msg
                continue
            if v["kind"] == "formula":
                names = self.names(v["id"])
                self._fn[v["id"]] = cm.compile_expr(v["expr"], names)
                self._refs[v["id"]] = {n: names[n] for n in cm.formula_names(v["expr"]) if n in names}
            elif v["kind"] == "process":
                p = self._proc.get(v["id"])
                if p is None or (p.op, p.param) != (v["op"], float(v["param"] or 0)):
                    self._proc[v["id"]] = Processor(v["op"], v["param"])
        # dependency order (depth-first); a cycle or a disabled dependency is an error
        order, state = [], {}

        def visit(v) -> str | None:
            vid = v["id"]
            if state.get(vid) == 2:
                return self.base.get(vid)
            if state.get(vid) == 1:
                self.errors[vid] = "Circular reference between variables."
                return None
            state[vid] = 1
            base = None
            for d in self._deps(v):
                if d in by_key:
                    dv = by_key[d]
                    if not dv["enabled"] or dv["id"] in self.errors:
                        self.errors.setdefault(vid, f"Uses '{dv['name']}', which is disabled or invalid.")
                        continue
                    b = visit(dv)
                else:
                    b = d[0]
                base = base or b
            state[vid] = 2
            if base is None:
                self.errors.setdefault(vid, "No source data.")
            elif vid not in self.errors:
                self.base[vid] = base
                order.append(v)
            return base

        for v in live:
            if v["id"] not in self.errors:
                visit(v)
        self.order = [v for v in order if v["id"] not in self.errors]

    def reset(self):
        for p in self._proc.values():
            p.reset()
        self._count.clear(); self.values.clear()

    # ---- evaluation
    def compute(self, tid: str, blk) -> list[tuple[dict, VarBlock]]:
        """Scaled block of topic `tid` -> [(variable, VarBlock)] for the variables it drives."""
        todo = [v for v in self.order if self.base.get(v["id"]) == tid]
        if not todo:
            return []
        out = []
        block = {(tid, ch): blk.y[r] for r, ch in enumerate(blk.channels)}
        for v in todo:
            vid = v["id"]
            try:
                if v["kind"] == "formula":
                    env = {}
                    for name, key in self._refs[vid].items():
                        val = block.get(key)
                        if val is None:
                            val = self.latest(key)
                            val = np.nan if val is None else val
                        env[name] = val
                    y = self._fn[vid](env)
                    if y.ndim == 0 or y.size == 1:
                        y = np.full(blk.n, float(np.ravel(y)[0]))
                    vb = VarBlock(blk.seq, blk.sample_idx, blk.t0, blk.dt, y.reshape(1, -1))
                elif v["kind"] == "process":
                    x = block.get(tuple(v["source"]))
                    if x is None:
                        continue
                    y = self._proc[vid](x, blk.dt)
                    vb = VarBlock(blk.seq, blk.sample_idx, blk.t0, blk.dt, y.reshape(1, -1))
                else:
                    src = tuple(v["source"])
                    win = float(v.get("window_s") or 0)
                    x = block.get(src)
                    fs = 1.0 / blk.dt
                    if win > 0 or x is None:
                        h = self.history(src, win or blk.n * blk.dt)
                        if h is not None:
                            x = h[1]
                            fs = an.sample_rate(h[0]) or fs
                    if x is None:
                        continue
                    val = an.stat(v.get("stat", "mean"), x, fs)
                    n = self._count[vid] = self._count.get(vid, 0) + 1
                    vb = VarBlock(blk.seq, n, blk.t0 + (blk.n - 1) * blk.dt, blk.n * blk.dt,
                                  np.array([[np.nan if val is None else val]]))
            except Exception:                          # never stop acquisition for a formula
                y = np.full(blk.n, np.nan)
                vb = VarBlock(blk.seq, blk.sample_idx, blk.t0, blk.dt, y.reshape(1, -1))
            block[var_key(vid)] = vb.y[0]
            last = vb.y[0, -1]
            self.values[vid] = float(last)
            out.append((v, vb))
        return out

    def is_scalar(self, v: dict) -> bool:
        return v.get("kind") == "statistic"
