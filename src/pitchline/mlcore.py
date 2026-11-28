"""The statistics behind the forecasts: count distributions, walk-forward validation, model candidates.

Shots on target per player-match are small counts with more variation than a Poisson allows (a player has
good and bad days beyond what his average predicts). So the forecast is a negative binomial: the model
supplies the mean, and one dispersion number, measured on matches the model never saw, supplies the spread.
That spread turns a mean into probabilities ("chance of at least one on target") and ranges.

Validation is walk-forward: several successive future periods are each predicted by a model trained only on
what came before, and every prediction is out-of-sample. A model is used only if it beats the baseline
clearly (a paired bootstrap over matches) and in most periods; otherwise the baseline is used.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("pitchline.ml")
MIN_ALPHA = 1e-6
LEVEL = 0.8                    # central interval shown for each forecast


# ------------------------------------------------------------------ distributions
def dist(mu, alpha: float):
    """Frozen count distribution with mean mu: Poisson with no extra spread, else negative binomial
    with variance mu + alpha * mu^2."""
    from scipy import stats

    mu = np.clip(np.asarray(mu, float), 1e-6, None)
    if alpha is None or alpha < MIN_ALPHA:
        return stats.poisson(mu)
    n = 1.0 / alpha
    return stats.nbinom(n, n / (n + mu))


def p_at_least(mu, alpha: float, k: int = 1):
    """P(count >= k)."""
    return 1.0 - dist(mu, alpha).cdf(k - 1)


def interval(mu, alpha: float, level: float = LEVEL):
    """Central interval (lo, hi) as integers: the (1-level)/2 and (1+level)/2 quantiles. For counts it holds
    at least `level` of the probability, and much more when the mean is small (counts of 0, 1, 2)."""
    d = dist(mu, alpha)
    return d.ppf((1 - level) / 2).astype(int), d.ppf((1 + level) / 2).astype(int)


def estimate_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    """Dispersion by moments: Var(y) = mu + alpha * mu^2, so alpha = sum((y-mu)^2 - y) / sum(mu^2)."""
    mu = np.clip(np.asarray(mu, float), 1e-6, None)
    y = np.asarray(y, float)
    return float(max(0.0, np.sum((y - mu) ** 2 - y) / np.sum(mu ** 2)))


# ------------------------------------------------------------------ metrics
def deviance_rows(y: np.ndarray, mu: np.ndarray) -> np.ndarray:
    mu = np.clip(mu, 1e-3, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 2 * (np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0) - (y - mu))


def deviance(y: np.ndarray, mu: np.ndarray) -> float:
    return float(np.mean(deviance_rows(y, mu)))


def metrics(y: np.ndarray, mu: np.ndarray) -> dict:
    return {"mae": round(float(np.mean(np.abs(y - mu))), 4), "deviance": round(deviance(y, mu), 4),
            "bias": round(float(np.mean(mu - y)), 4)}


def reliability(pred: np.ndarray, actual: np.ndarray, bins: int = 5) -> list[dict]:
    """Sort by prediction, split into equal groups, compare average prediction with average outcome."""
    out = []
    for chunk in np.array_split(np.argsort(pred), bins):
        if len(chunk):
            out.append({"predicted": round(float(pred[chunk].mean()), 3),
                        "actual": round(float(actual[chunk].mean()), 3), "n": int(len(chunk))})
    return out


def brier(p: np.ndarray, happened: np.ndarray) -> float:
    return float(np.mean((p - happened) ** 2))


def coverage(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.mean((y >= lo) & (y <= hi)))


# ------------------------------------------------------------------ walk-forward folds
@dataclass
class Fold:
    train: np.ndarray        # row indices
    test: np.ndarray
    start: str               # first date in the test block
    end: str


def walk_forward_folds(dates: list[str], k: int = 4, test_share: float = 0.4,
                       min_train: int = 3000, min_test: int = 200) -> list[Fold]:
    """Expanding-window folds over rows sorted by date. The newest `test_share` of the days is cut into
    `k` consecutive blocks; each is predicted by a model trained on every row dated strictly before it."""
    d = np.array(dates)
    days = sorted(set(dates))
    if len(days) < k * 2:
        return []
    test_days = days[int(len(days) * (1 - test_share)):]
    folds = []
    for block in np.array_split(np.array(test_days), k):
        if not len(block):
            continue
        start, end = str(block[0]), str(block[-1])
        train = np.nonzero(d < start)[0]
        test = np.nonzero((d >= start) & (d <= end))[0]
        if len(train) >= min_train and len(test) >= min_test:
            folds.append(Fold(train, test, start, end))
    return folds


# ------------------------------------------------------------------ candidates
def available_models() -> tuple[str, ...]:
    try:
        import lightgbm  # noqa: F401
        return ("poisson", "boosted", "lightgbm")
    except ImportError:
        return ("poisson", "boosted")


def fit(kind: str, X: np.ndarray, y: np.ndarray):
    if kind == "poisson":
        from sklearn.linear_model import PoissonRegressor
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(StandardScaler(), PoissonRegressor(alpha=1e-3, max_iter=500)).fit(X, y)
    if kind == "lightgbm":
        from lightgbm import LGBMRegressor
        return LGBMRegressor(objective="poisson", n_estimators=300, learning_rate=0.03, num_leaves=15,
                             min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                             reg_lambda=1.0, random_state=0, n_jobs=1, verbose=-1).fit(X, y)
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(loss="poisson", max_iter=150, learning_rate=0.05, max_depth=3,
                                         random_state=0).fit(X, y)


def predict_mean(model, X: np.ndarray) -> np.ndarray:
    return np.clip(model.predict(X), 1e-3, None)


# ------------------------------------------------------------------ is the model really better?
def paired_bootstrap(dev_base: np.ndarray, dev_model: np.ndarray, groups: np.ndarray,
                     draws: int = 1000, seed: int = 0) -> tuple[float, float, float]:
    """Mean per-row deviance improvement over the baseline, with a 95% interval from resampling whole
    matches (players in one match are not independent). Positive means the model is better."""
    diff = dev_base - dev_model
    _, idx = np.unique(groups, return_inverse=True)
    n_groups = idx.max() + 1
    sums = np.bincount(idx, weights=diff, minlength=n_groups)
    counts = np.bincount(idx, minlength=n_groups).astype(float)
    rng = np.random.default_rng(seed)
    boot = np.empty(draws)
    for start in range(0, draws, 50):                      # in chunks: light on memory with many matches
        pick = rng.integers(0, n_groups, size=(min(50, draws - start), n_groups))
        boot[start:start + len(pick)] = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    return float(diff.mean()), float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
