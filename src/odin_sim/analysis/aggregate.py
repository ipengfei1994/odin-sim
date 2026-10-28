"""实验结果的跨条件聚合与置信区间。

输入：一次 sweep 产物目录（含 results.csv）。
输出：按分组键聚合并对数值指标给出 n / mean / std / sem / ci_low / ci_high。

设计约束
--------
1. **仅依赖 numpy（核心依赖）**：聚合与统计不强制 pandas / scipy。CI 默认
   正态近似；若环境中装了 scipy，自动切换为更严谨的 t 分布区间。这样未安装
   ``[analysis]`` 可选依赖的用户也能跑基本聚合。
2. **不编造缺失**：某指标在某组全缺失或非数值非布尔时，该组该指标记 n=0 且
   统计量为 None，绝不拿空数据造一个统计量。
3. **数值与二值分治**：数值指标给 mean/std/sem/CI；布尔指标（如是否反噬）
   自动聚合成**发生率**（n / n_true / rate / 率 CI），因为实验里最关心的恰恰是
   这类率随治理强度的变化，丢弃会废掉核心指标的可用性。
3. **可复现**：聚合是 results.csv 的纯函数，不读模型、不跑仿真。
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from ..storage.manifest import read_results_csv

__all__ = [
    "load_results",
    "aggregate",
    "aggregate_to_csv",
    "estimate_ci",
]


def load_results(root: str | Path) -> list[dict]:
    """读取 results.csv 并把单元格尽力转为合适类型。

    - 空字符串 -> None
    - ``"True"`` / ``"False"`` -> bool
    - 可解析数字 -> float
    - 其余 -> 原字符串

    返回每行一个 dict 的列表（键顺序不保证，但内容完整）。
    """
    rows = read_results_csv(root)
    return [_coerce_row(r) for r in rows]


def _coerce_row(row: Mapping[str, str]) -> dict:
    out: dict = {}
    for key, value in row.items():
        out[key] = _coerce_cell(value)
    return out


def _coerce_cell(value: str | None):
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return float(text)
    except (TypeError, ValueError):
        return text


def estimate_ci(values: Sequence[float], confidence: float = 0.95) -> tuple[float, float]:
    """围绕均值给出 (ci_low, ci_high)。

    优先用 ``scipy.stats.t`` 的 t 区间（小样本更准确）；scipy 不可用时回退
    到常用置信度对应的正态近似（0.90/0.95/0.99），其余置信度也用 1.96 近似。
    """
    arr = np.asarray(list(values), dtype=float)
    n = int(arr.size)
    if n == 0:
        return (float("nan"), float("nan"))
    mean = float(arr.mean())
    if n == 1:
        return (mean, mean)
    sem = float(arr.std(ddof=1) / math.sqrt(n))
    half = _critical(confidence, n - 1) * sem
    return (mean - half, mean + half)


def _critical(confidence: float, df: int) -> float:
    try:
        from scipy import stats

        return float(stats.t.ppf(1.0 - (1.0 - confidence) / 2.0, df=df))
    except Exception:
        return {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence, 1.96)


def _z_for(confidence: float) -> float:
    """比例区间用的正态分位数（与数值 CI 的 t 区分）。"""
    try:
        from scipy import stats

        return float(stats.norm.ppf(1.0 - (1.0 - confidence) / 2.0))
    except Exception:
        return {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence, 1.96)


def _proportion_ci(n_true: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """二值指标发生率 (n_true/n) 的置信区间，正态近似并裁剪到 [0, 1]。"""
    if n == 0:
        return (float("nan"), float("nan"))
    if n == 1:
        p = 1.0 if n_true else 0.0
        return (p, p)
    p = n_true / n
    z = _z_for(confidence)
    half = z * math.sqrt(p * (1.0 - p) / n)
    return (max(0.0, p - half), min(1.0, p + half))


def aggregate(
    results: Sequence[Mapping],
    group_by: Sequence[str],
    metrics: Sequence[str],
    confidence: float = 0.95,
) -> list[dict]:
    """按 ``group_by`` 分组，对每个数值型 ``metric`` 计算统计量。

    参数
    ----
    results : sweep 的行（list[dict]，通常来自 ``load_results``）。
    group_by : 分组键（如 ``["mode", "beta_gov"]``）；缺失键按 ``""`` 处理。
    metrics : 要聚合的指标列名（如 ``["g3.backfire.bf_final"]``）。
    confidence : CI 置信度，默认 0.95。

    返回
    ----
    每行一个分组；含 group_by 原列、``n``，以及每个 metric 的
    ``<metric>.n / .mean / .std / .sem / .ci_low / .ci_high``。
    非数值（bool/str）或全缺失的 metric 在该组记 n=0 且统计量为 None。
    """
    groups: dict[tuple, list[dict]] = {}
    for rec in results:
        key = tuple(str(rec.get(g, "")) for g in group_by)
        groups.setdefault(key, []).append(rec)

    out: list[dict] = []
    for key, recs in groups.items():
        row: dict = {g: key[i] for i, g in enumerate(group_by)}
        row["n"] = len(recs)
        for m in metrics:
            nums = [
                rec.get(m)
                for rec in recs
                if isinstance(rec.get(m), (int, float)) and not isinstance(rec.get(m), bool)
            ]
            bools = [rec.get(m) for rec in recs if isinstance(rec.get(m), bool)]
            if nums:
                arr = np.asarray(nums, dtype=float)
                size = int(arr.size)
                mean = float(arr.mean())
                std = float(arr.std(ddof=1)) if size > 1 else 0.0
                sem = std / math.sqrt(size) if size > 1 else 0.0
                ci_low, ci_high = estimate_ci(nums, confidence)
                row[f"{m}.n"] = size
                row[f"{m}.mean"] = mean
                row[f"{m}.std"] = std
                row[f"{m}.sem"] = sem
                row[f"{m}.ci_low"] = ci_low
                row[f"{m}.ci_high"] = ci_high
            elif bools:
                # 二值指标（如是否反噬）：聚合成发生率而非丢弃
                n = len(bools)
                n_true = sum(1 for b in bools if b)
                rate = n_true / n if n else float("nan")
                ci_low, ci_high = _proportion_ci(n_true, n, confidence)
                row[f"{m}.n"] = n
                row[f"{m}.n_true"] = n_true
                row[f"{m}.rate"] = rate
                row[f"{m}.rate_ci_low"] = ci_low
                row[f"{m}.rate_ci_high"] = ci_high
            else:
                for suffix in ("n", "n_true", "rate", "rate_ci_low", "rate_ci_high"):
                    row[f"{m}.{suffix}"] = 0 if suffix == "n" else None
        out.append(row)

    out.sort(key=lambda r: tuple(str(r.get(g, "")) for g in group_by))
    return out


def aggregate_to_csv(
    results: Sequence[Mapping],
    group_by: Sequence[str],
    metrics: Sequence[str],
    out_path: str | Path,
    confidence: float = 0.95,
) -> Path:
    """聚合并写 CSV，返回路径。"""
    rows = aggregate(results, group_by, metrics, confidence)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return path

    fieldnames: list[str] = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)

    def _cell(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return "%.6g" % v
        return str(v)

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _cell(r.get(k)) for k in fieldnames})
    return path
