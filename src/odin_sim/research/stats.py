"""S5｜统计检验（纯 numpy / 标准库，不新增运行时依赖）。

状态声明（Q-38 未锁定）
----------------------
v2《配套必备：统计规范》列出的 Mann-Whitney U + FDR、Cohen's d、BF01、
重复次数、量表精度均为**建议值 · 需在规格中锁定**。本模块只提供计算能力，
输出一律标注 ``statistics_version="unlocked"``，且：

* 不得作为验收依据（Q-39：二手转述数值同样不得作为测试期望值）；
* 不得输出 pass/fail 布尔达标字段；
* BF01 为 BIC 近似，不是 JZS 精确贝叶斯因子，必须在报告中注明。

scipy 是 ``[analysis]`` 可选依赖，本模块**不依赖**它；正态近似 p 值在
小样本下不精确，仅用于观测与后续锁定口径后的对照。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

__all__ = [
    "STATISTICS_VERSION", "StatsError", "mann_whitney_u", "cohens_d",
    "benjamini_hochberg", "bf01_bic_approx", "compare_groups",
]

STATISTICS_VERSION = "unlocked"


class StatsError(ValueError):
    """统计输入非法。"""


def _as_array(values: Sequence[float], name: str) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        raise StatsError(f"{name} 不能为空")
    if not np.all(np.isfinite(arr)):
        raise StatsError(f"{name} 含非有限值")
    return arr


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def mann_whitney_u(x: Sequence[float], y: Sequence[float]) -> dict[str, float]:
    """Mann-Whitney U（含 ties 校正的正态近似 p 值，双侧）。

    返回 ``u1, u2, u, p_normal_approx, rank_sum_x, n1, n2, tie_corrected``。
    小样本精确分布未实现：口径锁定前不把它当作检验结论。
    """
    a, b = _as_array(x, "x"), _as_array(y, "y")
    n1, n2 = a.size, b.size
    combined = np.concatenate([a, b])
    order = np.argsort(combined, kind="stable")
    sorted_vals = combined[order]
    ranks = np.empty(combined.size, dtype=float)
    i = 0
    tie_corrected = False
    while i < combined.size:
        j = i + 1
        while j < combined.size and sorted_vals[j] == sorted_vals[i]:
            j += 1
        ranks[order[i:j]] = (i + j + 1) / 2.0  # 1-based 平均秩
        if j - i > 1:
            tie_corrected = True
        i = j
    rank_sum_x = float(ranks[:n1].sum())
    u1 = rank_sum_x - n1 * (n1 + 1) / 2.0
    u2 = float(n1 * n2) - u1
    u = min(u1, u2)
    mu = n1 * n2 / 2.0
    # ties 校正的方差
    _, counts = np.unique(combined, return_counts=True)
    tie_term = float((counts ** 3 - counts).sum())
    n = n1 + n2
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0.0
    if sigma2 <= 0:
        p = 1.0
    else:
        z = (abs(u - mu) - 0.5) / math.sqrt(sigma2)  # 连续性校正
        p = max(0.0, min(1.0, 2.0 * (1.0 - _normal_cdf(z))))
    return {
        "u1": float(u1), "u2": float(u2), "u": float(u),
        "rank_sum_x": rank_sum_x, "n1": int(n1), "n2": int(n2),
        "p_normal_approx": float(p),
        "tie_corrected": float(tie_corrected),
    }


def cohens_d(x: Sequence[float], y: Sequence[float]) -> float:
    """Cohen's d（合并标准差，ddof=1）。"""
    a, b = _as_array(x, "x"), _as_array(y, "y")
    if a.size < 2 or b.size < 2:
        return float("nan")
    pooled = math.sqrt(
        ((a.size - 1) * float(a.var(ddof=1)) + (b.size - 1) * float(b.var(ddof=1)))
        / (a.size + b.size - 2)
    )
    if pooled == 0:
        return float("nan")
    return float((a.mean() - b.mean()) / pooled)


def benjamini_hochberg(
    pvalues: Sequence[float], *, alpha: float = 0.05
) -> dict[str, Any]:
    """BH-FDR 校正。返回按输入顺序的 adjusted p 与拒绝标记。

    ``alpha`` 是**调用方给定**的参数，本模块不内置任何 v2 建议阈值。
    """
    ps = _as_array(pvalues, "pvalues")
    if not 0.0 < alpha < 1.0:
        raise StatsError("alpha 必须在 (0, 1) 内")
    if np.any((ps < 0) | (ps > 1)):
        raise StatsError("p 值必须在 [0, 1] 内")
    m = ps.size
    order = np.argsort(ps, kind="stable")
    adjusted_sorted = np.minimum.accumulate(
        (ps[order] * m / np.arange(1, m + 1))[::-1]
    )[::-1]
    adjusted = np.empty(m, dtype=float)
    adjusted[order] = np.minimum(adjusted_sorted, 1.0)
    rejected = adjusted <= alpha
    return {
        "adjusted_pvalues": [float(v) for v in adjusted],
        "rejected": [bool(v) for v in rejected],
        "alpha": float(alpha),
        "n_tests": int(m),
    }


def bf01_bic_approx(x: Sequence[float], y: Sequence[float]) -> float:
    """BF01 的 BIC 近似（**非精确贝叶斯因子**）。

    ``BF10 ≈ exp((BIC_null − BIC_alt)/2)``，返回其倒数 BF01。
    该近似依赖样本量与 t 统计量，不能替代预注册的 JZS-BF01；
    v2 要求「预注册并报 BF01 以支持无差异结论」，口径见 Q-38。
    """
    a, b = _as_array(x, "x"), _as_array(y, "y")
    n = a.size + b.size
    if n < 3 or a.size < 2 or b.size < 2:
        return float("nan")
    pooled_var = (
        (a.size - 1) * float(a.var(ddof=1)) + (b.size - 1) * float(b.var(ddof=1))
    ) / (n - 2)
    if pooled_var <= 0:
        return float("nan")
    se = math.sqrt(pooled_var * (1.0 / a.size + 1.0 / b.size))
    t = (float(a.mean()) - float(b.mean())) / se
    # 双样本 t 的 BIC 差近似：ΔBIC ≈ n·ln(1 + t²/(n−2))
    delta_bic = n * math.log1p(t * t / max(n - 2, 1))
    bf10 = math.exp(delta_bic / 2.0)
    return float(1.0 / bf10)


def compare_groups(x: Sequence[float], y: Sequence[float], *, alpha: float = 0.05) -> dict[str, Any]:
    """一次性给出全部观测量；**不含任何达标判定**。"""
    result: dict[str, Any] = {
        "statistics_version": STATISTICS_VERSION,
        "note": "统计口径未锁定（Q-38），仅供观测，不得作为验收依据；BF01 为 BIC 近似",
        "thresholds_locked": False,
    }
    result["mann_whitney_u"] = mann_whitney_u(x, y)
    result["cohens_d"] = cohens_d(x, y)
    result["bf01_bic_approx"] = bf01_bic_approx(x, y)
    result["bh_fdr"] = benjamini_hochberg(
        [result["mann_whitney_u"]["p_normal_approx"]], alpha=alpha
    )
    return result
