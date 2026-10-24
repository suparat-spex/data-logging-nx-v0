"""
analysis.py - signal analysis for views and variables (numpy only, no Qt).

Statistics (one number from a block of samples), the way sound & vibration and
process people use them:
  value   last sample            mean / median / min / max / P-P (max - min)
  SD      standard deviation     var    variance
  RMS     root mean square       peak   max |y|
  crest   peak / RMS (shocks, bearing damage raise it; a sine is 1.414)
  skew    skewness (asymmetry; 0 for a symmetric signal)
  kurt    kurtosis, Pearson (4th moment; 3 for Gaussian noise, > 3 = impulsive)
  freq    dominant frequency (Hz) from the FFT peak

Spectra:
  spectrum()     single-sided FFT of the newest `nfft` samples with a window:
                 peak amplitude, RMS amplitude, dB (re 1 unit RMS) or PSD (unit²/Hz)
  spectrogram()  STFT: |FFT| of overlapping segments, in dB -> time x frequency image
  histogram()    probability density / counts, plus a fitted normal curve
"""
from __future__ import annotations

import numpy as np


# ====================================================================== statistics
def _finite(y) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    return y[np.isfinite(y)]


def _moment_stats(y):
    m, sd = y.mean(), y.std()
    if sd == 0:
        return 0.0, 0.0
    z = (y - m) / sd
    return float(np.mean(z ** 3)), float(np.mean(z ** 4))


def dominant_freq(y, fs: float | None) -> float | None:
    if not fs or y.size < 8:
        return None
    f, a = spectrum(y, fs, "hann", "peak", nfft=min(y.size, 65536))
    if a.size < 2:
        return None
    i = int(np.argmax(a[1:])) + 1                       # skip DC
    return float(f[i])


STATS = {      # key: (column / menu label, f(y, fs) -> float | None); y is finite and not empty
    "value": ("Value", lambda y, fs: float(y[-1])),
    "mean": ("Mean", lambda y, fs: float(y.mean())),
    "min": ("Min", lambda y, fs: float(y.min())),
    "max": ("Max", lambda y, fs: float(y.max())),
    "sd": ("SD", lambda y, fs: float(y.std())),
    "rms": ("RMS", lambda y, fs: float(np.sqrt(np.mean(y * y)))),
    "pp": ("P-P", lambda y, fs: float(y.max() - y.min())),
    "peak": ("Peak", lambda y, fs: float(np.abs(y).max())),
    "crest": ("Crest", lambda y, fs: (lambda r: float(np.abs(y).max() / r) if r > 0 else None)(
        float(np.sqrt(np.mean(y * y))))),
    "var": ("Variance", lambda y, fs: float(y.var())),
    "median": ("Median", lambda y, fs: float(np.median(y))),
    "skew": ("Skewness", lambda y, fs: _moment_stats(y)[0]),
    "kurt": ("Kurtosis", lambda y, fs: _moment_stats(y)[1]),
    "freq": ("Dom. freq", dominant_freq),
}
STAT_KEYS = list(STATS)
STAT_LABEL = {k: v[0] for k, v in STATS.items()}
STAT_HELP = {
    "crest": "Peak / RMS - rises with shocks and impacts (sine = 1.414)",
    "kurt": "Pearson kurtosis - 3 for Gaussian noise, above 3 = impulsive (bearing faults)",
    "skew": "Skewness - 0 for a symmetric signal",
    "freq": "Dominant frequency (Hz) - the highest FFT peak above DC",
}


def stat(key: str, y, fs: float | None = None) -> float | None:
    y = _finite(y)
    if y.size == 0:
        return None
    try:
        with np.errstate(all="ignore"):
            v = STATS[key][1](y, fs)
    except (KeyError, ValueError, FloatingPointError):
        return None
    return v if v is None or np.isfinite(v) else None


def stats(y, fs: float | None = None, keys=None) -> dict[str, float | None]:
    """{key: value} for the given keys (default: all). Empty input -> all None."""
    keys = list(keys or STAT_KEYS)
    y = _finite(y)
    if y.size == 0:
        return dict.fromkeys(keys)
    return {k: stat(k, y, fs) for k in keys}


def sample_rate(t) -> float | None:
    """Sample rate from a time vector (median step, ignores gaps)."""
    t = np.asarray(t, dtype=float)
    if t.size < 3:
        return None
    d = np.diff(t[np.isfinite(t)])
    d = d[d > 0]
    return float(1.0 / np.median(d)) if d.size else None


# ====================================================================== windows
WINDOWS = {"hann": "Hann", "hamming": "Hamming", "blackman": "Blackman", "flattop": "Flat top",
           "rect": "Rectangular (none)"}
_FLATTOP = (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368)


def window(name: str, n: int) -> np.ndarray:
    if n <= 1 or name == "rect":
        return np.ones(n)
    if name == "hamming":
        return np.hamming(n)
    if name == "blackman":
        return np.blackman(n)
    if name == "flattop":                   # best amplitude accuracy for tones
        k = 2 * np.pi * np.arange(n) / (n - 1)
        return sum(((-1) ** i) * a * np.cos(i * k) for i, a in enumerate(_FLATTOP))
    return np.hanning(n)


# ====================================================================== spectra
SPEC_SCALES = {"peak": "Amplitude (peak)", "rms": "Amplitude (RMS)", "db": "dB re 1 unit RMS",
               "psd": "Power spectral density (unit²/Hz)"}
NFFT_CHOICES = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]


def spectrum(y, fs: float, win: str = "hann", scale: str = "rms", nfft: int = 4096,
             remove_dc: bool = True):
    """Single-sided spectrum of the newest `nfft` samples -> (f [Hz], values)."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)][-int(nfft):]
    n = y.size
    if n < 4 or not fs:
        return np.zeros(0), np.zeros(0)
    if remove_dc:
        y = y - y.mean()
    w = window(win, n)
    X = np.fft.rfft(y * w)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    if scale == "psd":
        p = (np.abs(X) ** 2) / (fs * np.sum(w * w))
        p[1:-1 if n % 2 == 0 else None] *= 2
        return f, p
    a = np.abs(X) / np.sum(w)                       # coherent gain correction
    a[1:-1 if n % 2 == 0 else None] *= 2            # single-sided peak amplitude
    if scale == "peak":
        return f, a
    rms = a / np.sqrt(2)
    rms[0] = a[0]
    if scale == "db":
        return f, 20 * np.log10(np.maximum(rms, 1e-12))
    return f, rms


def spectrogram(y, fs: float, nperseg: int = 512, overlap: float = 0.5, win: str = "hann"):
    """STFT magnitude in dB re 1 unit RMS -> (t [s from start], f [Hz], S [frames x bins])."""
    y = np.asarray(y, dtype=float)
    y = np.where(np.isfinite(y), y, 0.0)
    nperseg = int(max(8, nperseg))
    if y.size < nperseg or not fs:
        return np.zeros(0), np.zeros(0), np.zeros((0, 0))
    step = max(1, int(nperseg * (1 - min(max(overlap, 0.0), 0.95))))
    nfr = 1 + (y.size - nperseg) // step
    idx = np.arange(nperseg)[None, :] + step * np.arange(nfr)[:, None]
    seg = y[idx]
    seg = seg - seg.mean(axis=1, keepdims=True)
    w = window(win, nperseg)
    X = np.fft.rfft(seg * w, axis=1)
    a = np.abs(X) / np.sum(w) * 2 / np.sqrt(2)
    S = 20 * np.log10(np.maximum(a, 1e-12))
    t = (np.arange(nfr) * step + nperseg / 2) / fs
    f = np.fft.rfftfreq(nperseg, 1.0 / fs)
    return t, f, S


def histogram(y, bins: int = 50, density: bool = True):
    """-> (edges, heights, normal_fit(x) or None). The fit uses the sample mean and SD."""
    y = _finite(y)
    if y.size < 2:
        return np.zeros(0), np.zeros(0), None
    h, edges = np.histogram(y, bins=int(max(2, bins)), density=density)
    m, sd = float(y.mean()), float(y.std())
    if sd == 0:
        return edges, h, None
    scale = 1.0 if density else y.size * (edges[1] - edges[0])

    def fit(x):
        return scale * np.exp(-0.5 * ((x - m) / sd) ** 2) / (sd * np.sqrt(2 * np.pi))
    return edges, h, fit
