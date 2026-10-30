"""Small, dependency-light machine learning algorithms built on numpy.

Two models are enough for this project:

* RidgeRegression   - linear regression with L2 regularisation, used to
                      forecast how many blood units will be requested.
* LogisticRegression - binary classifier trained with Newton's method (IRLS),
                      used for donor-return and deferral-risk predictions.

Every model can be converted to a plain dict (to_dict) and rebuilt from it
(from_dict), so trained models are stored as readable JSON files instead of
pickles. JSON files cannot execute code when they are loaded.
"""

import math

import numpy as np


class StandardScaler:
    """Rescale every feature to mean 0 and standard deviation 1."""

    def __init__(self, mean=None, std=None):
        self.mean = None if mean is None else np.asarray(mean, dtype=float)
        self.std = None if std is None else np.asarray(std, dtype=float)

    def fit(self, X):
        X = np.asarray(X, dtype=float)
        self.mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0  # constant columns stay unchanged
        self.std = std
        return self

    def transform(self, X):
        return (np.asarray(X, dtype=float) - self.mean) / self.std

    def fit_transform(self, X):
        return self.fit(X).transform(X)

    def to_dict(self):
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}

    @classmethod
    def from_dict(cls, data):
        return cls(mean=data["mean"], std=data["std"])


class RidgeRegression:
    """Linear regression with L2 penalty, solved in closed form."""

    def __init__(self, alpha=1.0):
        self.alpha = alpha
        self.scaler = StandardScaler()
        self.coef = None
        self.intercept = 0.0

    def fit(self, X, y):
        Xs = self.scaler.fit_transform(X)
        y = np.asarray(y, dtype=float)
        self.intercept = float(y.mean())
        yc = y - self.intercept
        n_features = Xs.shape[1]
        A = Xs.T @ Xs + self.alpha * np.eye(n_features)
        self.coef = np.linalg.solve(A, Xs.T @ yc)
        return self

    def predict(self, X):
        return self.scaler.transform(X) @ self.coef + self.intercept

    def to_dict(self):
        return {
            "algorithm": "ridge_regression",
            "alpha": self.alpha,
            "scaler": self.scaler.to_dict(),
            "coef": self.coef.tolist(),
            "intercept": self.intercept,
        }

    @classmethod
    def from_dict(cls, data):
        model = cls(alpha=data["alpha"])
        model.scaler = StandardScaler.from_dict(data["scaler"])
        model.coef = np.asarray(data["coef"], dtype=float)
        model.intercept = float(data["intercept"])
        return model


def _sigmoid(z):
    z = np.clip(z, -35, 35)
    return 1.0 / (1.0 + np.exp(-z))


class LogisticRegression:
    """Binary logistic regression trained with Newton's method.

    balanced=True gives the rarer class more weight, which matters for
    deferrals (only a small share of visits end in deferral).
    """

    def __init__(self, l2=1.0, balanced=False, max_iter=50, tol=1e-8):
        self.l2 = l2
        self.balanced = balanced
        self.max_iter = max_iter
        self.tol = tol
        self.scaler = StandardScaler()
        self.coef = None
        self.intercept = 0.0

    def fit(self, X, y):
        Xs = self.scaler.fit_transform(X)
        y = np.asarray(y, dtype=float)
        n, d = Xs.shape
        Xb = np.hstack([np.ones((n, 1)), Xs])  # column 0 is the intercept

        weights = np.ones(n)
        if self.balanced:
            pos = y.sum()
            neg = n - pos
            if pos > 0 and neg > 0:
                weights = np.where(y == 1, n / (2 * pos), n / (2 * neg))

        penalty = np.full(d + 1, self.l2)
        penalty[0] = 0.0  # never penalise the intercept
        beta = np.zeros(d + 1)
        for _ in range(self.max_iter):
            p = _sigmoid(Xb @ beta)
            gradient = Xb.T @ (weights * (p - y)) + penalty * beta
            w = weights * p * (1 - p)
            hessian = (Xb * w[:, None]).T @ Xb + np.diag(penalty) + 1e-9 * np.eye(d + 1)
            step = np.linalg.solve(hessian, gradient)
            beta -= step
            if np.max(np.abs(step)) < self.tol:
                break
        self.intercept = float(beta[0])
        self.coef = beta[1:]
        return self

    def decision_function(self, X):
        return self.scaler.transform(X) @ self.coef + self.intercept

    def predict_proba(self, X):
        return _sigmoid(self.decision_function(X))

    def contributions(self, x):
        """Per-feature contribution to the log-odds for one sample."""
        xs = self.scaler.transform(np.asarray(x, dtype=float).reshape(1, -1))[0]
        return xs * self.coef

    def to_dict(self):
        return {
            "algorithm": "logistic_regression",
            "l2": self.l2,
            "balanced": self.balanced,
            "scaler": self.scaler.to_dict(),
            "coef": self.coef.tolist(),
            "intercept": self.intercept,
        }

    @classmethod
    def from_dict(cls, data):
        model = cls(l2=data["l2"], balanced=data.get("balanced", False))
        model.scaler = StandardScaler.from_dict(data["scaler"])
        model.coef = np.asarray(data["coef"], dtype=float)
        model.intercept = float(data["intercept"])
        return model


# ---------------------------------------------------------------- metrics ---

def mean_absolute_error(y_true, y_pred):
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def root_mean_squared_error(y_true, y_pred):
    return float(math.sqrt(np.mean((np.asarray(y_true) - np.asarray(y_pred)) ** 2)))


def roc_auc(y_true, scores):
    """Area under the ROC curve (probability a positive outranks a negative)."""
    y_true = np.asarray(y_true)
    scores = np.asarray(scores, dtype=float)
    pos = y_true == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    order = scores.argsort()
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    # average ranks for tied scores
    for value in np.unique(scores):
        tied = scores == value
        if tied.sum() > 1:
            ranks[tied] = ranks[tied].mean()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def classification_report(y_true, probabilities, threshold=0.5):
    y_true = np.asarray(y_true).astype(int)
    probabilities = np.asarray(probabilities, dtype=float)
    y_pred = (probabilities >= threshold).astype(int)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "auc": roc_auc(y_true, probabilities),
        "accuracy": (tp + tn) / len(y_true) if len(y_true) else None,
        "precision": precision,
        "recall": recall,
        "brier": float(np.mean((probabilities - y_true) ** 2)),
        "positive_rate": float(y_true.mean()) if len(y_true) else None,
        "threshold": threshold,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }
