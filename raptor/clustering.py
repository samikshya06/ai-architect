import numpy as np
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture

RANDOM_STATE = 42


def _best_k(X: np.ndarray, max_k: int) -> int:
    """Pick the number of GMM components with the lowest BIC."""
    max_k = max(1, min(max_k, len(X) - 1))
    best_k, best_bic = 1, np.inf
    for k in range(1, max_k + 1):
        gmm = GaussianMixture(
            n_components=k, covariance_type="diag", random_state=RANDOM_STATE
        ).fit(X)
        bic = gmm.bic(X)
        if bic < best_bic:
            best_k, best_bic = k, bic
    return best_k


def cluster_embeddings(
    embeddings: list[list[float]],
    max_clusters: int = 10,
    soft_threshold: float = 0.25,
) -> list[list[int]]:
    """Return lists of item indices, one list per cluster.

    Items may belong to several clusters (soft assignment above the threshold).
    Embeddings are PCA-reduced first so diagonal GMMs stay well-conditioned.
    """
    n = len(embeddings)
    if n <= 3:
        return [list(range(n))]

    X = np.asarray(embeddings, dtype=float)
    dims = max(2, min(10, n - 2, X.shape[1]))
    X = PCA(n_components=dims, random_state=RANDOM_STATE).fit_transform(X)

    k = _best_k(X, max_clusters)
    if k == 1:
        return [list(range(n))]

    gmm = GaussianMixture(
        n_components=k, covariance_type="diag", random_state=RANDOM_STATE
    ).fit(X)
    probs = gmm.predict_proba(X)

    clusters: list[list[int]] = [[] for _ in range(k)]
    for i, row in enumerate(probs):
        members = np.flatnonzero(row > soft_threshold)
        if len(members) == 0:
            members = [int(row.argmax())]
        for c in members:
            clusters[int(c)].append(i)
    return [c for c in clusters if c]
