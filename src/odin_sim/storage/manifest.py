"""产物序列化：单次运行的 manifest 与 sweep 汇总表。

设计约束
--------
1. **manifest 自描述**：拿到一个 run 目录，不看代码也能知道这次运行用了
   什么规格、什么模型、什么环境、产物在哪、成功与否。
2. **环境只记录、不参与续跑判定**：换台机器重跑同一个 run_id 时，环境不同
   是事实，但不应因此把已完成的结果判为失效——续跑只看规格与状态。
3. **失败也要落盘**：失败的运行留下 status=error 与错误信息，不留"看起来
   跑过"的空目录。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .layout import (
    manifest_path,
    results_csv_path,
    run_dir,
    sweep_manifest_path,
    timeseries_path,
)

__all__ = [
    "MANIFEST_VERSION",
    "STATUS_ERROR",
    "STATUS_OK",
    "read_manifest",
    "read_results_csv",
    "read_timeseries_csv",
    "write_manifest",
    "write_results_csv",
    "write_sweep_manifest",
    "write_timeseries_csv",
]

MANIFEST_VERSION = "odin-sim-manifest-v1"
STATUS_OK = "ok"
STATUS_ERROR = "error"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_manifest(
    run_dir_path: str | Path,
    *,
    run_id: str,
    spec: Mapping[str, Any],
    model: Mapping[str, Any],
    result: Mapping[str, Any],
    environment: Mapping[str, Any],
    metrics: Mapping[str, Any] | None = None,
    skipped_metrics: Sequence[Mapping[str, Any]] = (),
    artifacts: Sequence[str] = (),
    status: str = STATUS_OK,
    error: Mapping[str, Any] | None = None,
    role: str = "main",
    control_run_id: str | None = None,
    control_status: str = "none",
) -> Path:
    """写单次运行的 manifest，返回文件路径。

    ``role`` / ``control_*`` 描述本次 sweep 对这条运行的用法（主运行还是
    对照、与谁配对），不属于运行规格本身，因此不进 ``spec``，也不影响 run_id。
    """
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "status": status,
        "created_at": _now(),
        "role": role,
        "control_run_id": control_run_id,
        "control_status": control_status,
        "spec": dict(spec),
        "model": dict(model),
        "result": dict(result),
        "metrics": dict(metrics or {}),
        "skipped_metrics": [dict(m) for m in skipped_metrics],
        "artifacts": list(artifacts),
        "environment": dict(environment),
        "error": dict(error) if error else None,
    }
    path = manifest_path(run_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return path


def read_manifest(run_dir_path: str | Path) -> dict[str, Any] | None:
    """读取 manifest；不存在或不是本版本的 manifest 返回 None。"""
    path = manifest_path(run_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if payload.get("manifest_version") != MANIFEST_VERSION:
        return None
    return dict(payload)


STEP_COLUMN = "step"
"""时间序列 CSV 的时间步列名。"""


def write_timeseries_csv(
    run_dir_path: str | Path,
    *,
    series_ord: Mapping[str, Any],
    series_all: Mapping[str, Any],
) -> Path | None:
    """写逐时间步序列，列为 ``step, ord_<key>..., all_<key>...``。

    两组含义与 manifest 里的 ``series_final_ord`` / ``series_final_all`` 一致，
    这里存的是**整条曲线**而非末值。

    设计约束
    --------
    1. **不截断精度**：不复用 ``_fmt_cell``（``%.6g`` 会丢精度）。末值必须与
       manifest 里已存的 ``series_final_*`` 精确相等，这是时间序列的正确性锚点。
    2. **长度必须一致**：各条序列属于同一次运行的同一条时间轴，长度不同说明
       模型内部不一致，报出来而不是对齐到最短。
    3. 序列为空（模型不提供）时不写文件——不留空表冒充有数据。
    """
    ord_arrays = {k: np.asarray(v).ravel() for k, v in series_ord.items()}
    all_arrays = {k: np.asarray(v).ravel() for k, v in series_all.items()}
    if not ord_arrays and not all_arrays:
        return None

    sizes = {arr.size for arr in (*ord_arrays.values(), *all_arrays.values())}
    if len(sizes) != 1:
        detail = ", ".join(f"{k}={a.size}" for k, a in (*ord_arrays.items(), *all_arrays.items()))
        raise ValueError(f"各时间序列长度不一致，无法对齐：{detail}")
    n_steps = sizes.pop()

    path = timeseries_path(run_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        [STEP_COLUMN]
        + [f"ord_{k}" for k in ord_arrays]
        + [f"all_{k}" for k in all_arrays]
    )
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for t in range(n_steps):
            row: dict[str, Any] = {STEP_COLUMN: t}
            for key, arr in ord_arrays.items():
                row[f"ord_{key}"] = float(arr[t])
            for key, arr in all_arrays.items():
                row[f"all_{key}"] = float(arr[t])
            writer.writerow(row)
    return path


def read_timeseries_csv(run_dir_path: str | Path) -> dict[str, Any] | None:
    """读时间序列；文件不存在或为空返回 None。"""
    path = timeseries_path(run_dir_path)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None

    columns = [c for c in rows[0] if c != STEP_COLUMN]
    out: dict[str, Any] = {STEP_COLUMN: np.array([int(r[STEP_COLUMN]) for r in rows])}
    for col in columns:
        out[col] = np.array([float(r[col]) for r in rows], dtype=float)
    return out


def write_sweep_manifest(root: str | Path, payload: Mapping[str, Any]) -> Path:
    path = sweep_manifest_path(root)
    Path(root).mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return path


def write_results_csv(root: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path | None:
    """写汇总表。无行时不写文件（不留空表冒充结果）。返回路径或 None。"""
    if not rows:
        return None
    path = results_csv_path(root)
    Path(root).mkdir(parents=True, exist_ok=True)

    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt_cell(row.get(k)) for k in fieldnames})
    return path


def read_results_csv(root: str | Path) -> list[dict[str, str]]:
    path = results_csv_path(root)
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _fmt_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return "%.6g" % value
    return str(value)


def run_dir_for(root: str | Path, run_id: str) -> Path:
    """供实验层复用目录规则（避免调用方自己拼路径）。"""
    return run_dir(root, run_id)
