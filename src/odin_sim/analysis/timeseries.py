"""时间序列分析：把每条 run 的逐时间步序列聚合成带置信带的曲线。

与 ``aggregate.py`` 的分工
--------------------------
``aggregate`` 看的是**终值**（每次运行一个数）， ``timeseries`` 看的是
**整条轨迹摘要**（每次运行一条长度为 ``steps+1`` 的序列）。两者共用同一套
约定：默认只看主运行、缺失不参与而不填零、置信区间的临界值取自同一函数。

数据来源
--------
序列由**模型**在运行时产出并落盘为 ``runs/<run_id>/timeseries.csv``。本模块
不重新解释轨迹、不复制任何模型的公式——平台只负责读、对齐、聚合、渲染。
摘要口径（例如"极端"如何判定）永远由模型定义。
"""
from __future__ import annotations

import csv
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ..storage import layout
from ..storage.manifest import read_manifest, read_timeseries_csv
from .aggregate import _critical

__all__ = [
    "STEP_COLUMN",
    "aggregate_timeseries",
    "load_run_series",
    "timeseries_to_csv",
]

STEP_COLUMN = "step"


def load_run_series(
    root: str | Path,
    *,
    role: str = "main",
    spec_keys: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """读取一个 sweep 下所有运行的时间序列。

    每个元素形如 ``{"run_id": ..., **spec, "step": ndarray, "ord_extreme": ndarray, ...}``
    —— ``step`` 之外的数组列就是模型的序列输出（``ord_*`` / ``all_*``）。

    参数
    ----
    root:
        sweep 产物根目录。
    role:
        ``"main"``（默认）只看主运行； ``"control"`` 只看对照； ``"all"`` 全部。
        对照是配对基线而非实验条件，混入会造成伪重复，理由同 ``filter_by_role``。
    spec_keys:
        要携带的运行规格键（如 ``["mode", "beta_gov", "tau"]``）。留空表示
        把 manifest 里的 spec 全部带上。

    缺 ``timeseries.csv`` 的运行一律跳过；若一个都没有则返回空列表，由调用方
    决定报错还是提示重跑（不在这里静默编造空曲线）。
    """
    root_path = Path(root)
    records: list[dict[str, Any]] = []
    runs_root = root_path / layout.RUNS_DIRNAME
    if not runs_root.is_dir():
        return records

    for run_path in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        manifest = read_manifest(run_path)
        if manifest is None or manifest.get("status") != "ok":
            continue
        if role != "all" and str(manifest.get("role", "main")) != role:
            continue
        series = read_timeseries_csv(run_path)
        if series is None:
            continue

        spec = dict(manifest.get("spec") or {})
        record: dict[str, Any] = {"run_id": manifest.get("run_id", run_path.name)}
        if spec_keys:
            for key in spec_keys:
                record[key] = spec.get(key, "")
        else:
            for key, value in spec.items():
                record[key] = value if not isinstance(value, (dict, list)) else str(value)
        record.update(series)
        records.append(record)
    return records


def aggregate_timeseries(
    records: Sequence[Mapping[str, Any]],
    group_by: Sequence[str],
    columns: Sequence[str] | None = None,
    *,
    confidence: float = 0.95,
) -> list[dict[str, Any]]:
    """跨种子把序列聚合成``mean / std / ci_low / ci_high`` 逐点数组。

    返回每个分组一个 dict：分组键 + ``step`` 数组 + 每个列的
    ``<col>.n / .mean / .std / .ci_low / .ci_high`` 数组。

    约束
    ----
    1. 同组内所有运行的 ``step`` 长度必须一致；不同则报错并指出是哪个 run——
       长度不同意味着它们跑的不是同一条时间轴，平均没有意义。
    2. 某列在某些运行里缺失时，只聚合有该列的运行， ``n`` 如实记录，不用别的
       值补齐。
    """
    if not records:
        return []

    known_cols = [c for c in records[0] if c not in ("run_id", STEP_COLUMN) and isinstance(records[0][c], np.ndarray)]
    targets = list(columns) if columns else known_cols

    groups: dict[tuple, list[Mapping[str, Any]]] = {}
    for rec in records:
        key = tuple(str(rec.get(k, "")) for k in group_by)
        groups.setdefault(key, []).append(rec)

    out: list[dict[str, Any]] = []
    for key, recs in groups.items():
        lengths = {int(np.asarray(r[STEP_COLUMN]).size) for r in recs}
        if len(lengths) != 1:
            detail = ", ".join(f"{str(r.get('run_id'))[:8]}={int(np.asarray(r[STEP_COLUMN]).size)}" for r in recs)
            raise ValueError(f"同一分组内时间步数不一致，无法对齐：{detail}")
        n_steps = lengths.pop()

        row: dict[str, Any] = dict(zip(group_by, key))
        row[STEP_COLUMN] = np.asarray(recs[0][STEP_COLUMN])
        for col in targets:
            present = [np.asarray(r[col], dtype=float) for r in recs if r.get(col) is not None]
            if not present:
                for suffix in ("n", "mean", "std", "ci_low", "ci_high"):
                    row[f"{col}.{suffix}"] = 0 if suffix == "n" else None
                continue
            stack = np.vstack(present)
            # 比例量（如"极端观点占比"）的真值域是 [0,1]。正态/t 近似在接近边界时
            # 会算出负的下界或超过 1 的上界——那是近似方法的伪影，不是真实不确定性。
            # 因此沿袭 aggregate._proportion_ci 的约定，裁掉越界部分。
            clip = _looks_like_proportion(stack)
            mean, ci_low, ci_high = _mean_ci(stack, confidence, clip=clip)
            row[f"{col}.proportion"] = clip
            row[f"{col}.n"] = int(stack.shape[0])
            row[f"{col}.mean"] = mean
            row[f"{col}.std"] = stack.std(axis=0, ddof=1) if stack.shape[0] > 1 else np.zeros(n_steps)
            row[f"{col}.ci_low"] = ci_low
            row[f"{col}.ci_high"] = ci_high
        out.append(row)
    return out


def timeseries_to_csv(rows: Sequence[Mapping[str, Any]], out: str | Path, columns: Sequence[str]) -> Path:
    """把聚合结果写成长表 CSV：分组键 + step + 每列的均值与置信上下界。

    长表（每个时间点一行）便于外部工具消费：pandas、R、Excel 都能直接读。
    """
    if not rows:
        raise ValueError("没有可写入的时间序列聚合结果")
    stat_suffix = (".n", ".mean", ".std", ".ci_low", ".ci_high", ".proportion")
    group_keys = [
        k
        for k in rows[0]
        if k != STEP_COLUMN and not k.endswith(stat_suffix)
    ]
    fieldnames = [*group_keys, STEP_COLUMN]
    for col in columns:
        fieldnames.extend([f"{col}.mean", f"{col}.ci_low", f"{col}.ci_high"])

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            steps = np.asarray(row[STEP_COLUMN])
            for t in range(int(steps.size)):
                line: dict[str, Any] = {k: row.get(k, "") for k in group_keys}
                line[STEP_COLUMN] = int(steps[t])
                for col in columns:
                    mean = row.get(f"{col}.mean")
                    low = row.get(f"{col}.ci_low")
                    high = row.get(f"{col}.ci_high")
                    line[f"{col}.mean"] = "" if mean is None else float(np.asarray(mean)[t])
                    line[f"{col}.ci_low"] = "" if low is None else float(np.asarray(low)[t])
                    line[f"{col}.ci_high"] = "" if high is None else float(np.asarray(high)[t])
                writer.writerow(line)
    return path


def _looks_like_proportion(values: np.ndarray) -> bool:
    """按值域判断某列是否为比例量（启发式，结论写进产物的 ``<col>.proportion``）。

    只有当**全部取值**都落在 ``[0, 1]`` 才判定为比例。非比例量一旦越界就不裁
    剪，因此不会把值域本就超出 ``[0,1]`` 的序列硬夹进去。代价是：极少数值域
    恰好落在 ``[0,1]`` 的非比例列会被保守裁剪——宁可保守，也不要画出负的"占比"。

   真正的判定依据是模型的语义声明，本平台目前没有该字段，故此处如实标注为启发式。
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return False
    return bool(finite.min() >= 0.0 and finite.max() <= 1.0)


def _mean_ci(
    stack: np.ndarray,
    confidence: float,
    *,
    clip: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """沿第 0 维（种子维）求均值与置信带，逐点计算但不重定口径。

    临界值与 ``aggregate.estimate_ci`` 取自同一个 ``_critical``，保证终值口径与
    逐点口径不会各说一套。
    """
    n = int(stack.shape[0])
    mean = stack.mean(axis=0)
    if n < 2:
        return mean, mean.copy(), mean.copy()
    std = stack.std(axis=0, ddof=1)
    sem = std / math.sqrt(n)
    crit = _critical(confidence, n - 1)
    low = mean - crit * sem
    high = mean + crit * sem
    if clip:
        low = np.clip(low, 0.0, None)
        high = np.clip(high, None, 1.0)
    return mean, low, high
