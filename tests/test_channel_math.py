"""Scaling and the formula sandbox: project files from other people must not run code."""
import numpy as np
import pytest

from nxlogger.core import channel_math as cm


def test_linear_and_two_point():
    assert cm.make_scaler({"type": "linear", "m": 1.0, "b": 0.0}) is None      # identity: no work
    f = cm.make_scaler({"type": "linear", "m": 2.0, "b": 1.0})
    assert list(f(np.array([0.0, 1.0]))) == [1.0, 3.0]
    g = cm.make_scaler({"type": "twopoint", "x1": 4, "y1": 0, "x2": 20, "y2": 100})    # 4-20 mA
    assert g(np.array([12.0]))[0] == pytest.approx(50.0)


def test_formula():
    f = cm.compile_formula("x * 9/5 + 32")
    assert list(f(np.array([0.0, 100.0]))) == [32.0, 212.0]


@pytest.mark.parametrize("expr", ["__import__('os')", "x.__class__", "open('f')", "'a'", "(lambda: 1)()"])
def test_formula_sandbox_rejects(expr):
    with pytest.raises(ValueError):
        cm.compile_formula(expr)
