"""Signal analysis (no Qt): statistics, spectrum scaling, spectrogram, histogram."""
import numpy as np
import pytest

from nxlogger.core import analysis as an

FS = 1000.0
T = np.arange(4096) / FS


def test_sine_statistics():
    y = 2.0 * np.sin(2 * np.pi * 50 * T)
    s = an.stats(y, FS)
    assert s["rms"] == pytest.approx(2 / np.sqrt(2), rel=1e-3)
    assert s["crest"] == pytest.approx(np.sqrt(2), rel=1e-2)
    assert s["pp"] == pytest.approx(4.0, rel=1e-3)
    assert s["freq"] == pytest.approx(50.0, abs=FS / 4096)
    assert s["kurt"] == pytest.approx(1.5, rel=1e-2)          # sine: 1.5, Gaussian: 3


def test_gaussian_kurtosis_and_empty():
    y = np.random.default_rng(1).normal(0, 1, 200_000)
    assert an.stat("kurt", y) == pytest.approx(3.0, abs=0.05)
    assert an.stat("skew", y) == pytest.approx(0.0, abs=0.02)
    assert an.stats([np.nan, np.nan])["mean"] is None


@pytest.mark.parametrize("win", ["hann", "flattop", "rect"])
def test_spectrum_amplitude(win):
    y = 3.0 * np.sin(2 * np.pi * 125 * T)                    # on a bin: no leakage for rect
    f, a = an.spectrum(y, FS, win, "peak", nfft=4096)
    i = int(np.argmax(a))
    assert f[i] == pytest.approx(125, abs=0.3)
    assert a[i] == pytest.approx(3.0, rel=0.02 if win != "hann" else 0.16)
    f, r = an.spectrum(y, FS, "flattop", "rms", nfft=4096)
    assert r.max() == pytest.approx(3.0 / np.sqrt(2), rel=0.02)


def test_psd_integrates_to_variance():
    y = np.random.default_rng(2).normal(0, 0.5, 4096)
    f, p = an.spectrum(y, FS, "hann", "psd", nfft=4096)
    assert np.sum(p) * (f[1] - f[0]) == pytest.approx(y.var(), rel=0.1)


def test_spectrogram_and_histogram_shapes():
    y = np.sin(2 * np.pi * 100 * T)
    t, f, S = an.spectrogram(y, FS, 256, 0.5)
    assert S.shape == (t.size, f.size) and f[np.argmax(S[0])] == pytest.approx(100, abs=4)
    edges, h, fit = an.histogram(np.random.default_rng(3).normal(5, 2, 50_000), 60)
    assert len(edges) == 61 and np.sum(h * np.diff(edges)) == pytest.approx(1.0)
    assert fit(np.array([5.0]))[0] == pytest.approx(1 / (2 * np.sqrt(2 * np.pi)), rel=0.02)
