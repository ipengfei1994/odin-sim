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

from .layout import manifest_path, results_csv_path, run_dir, sweep_manifest_path

__all__ = [
    "MANIFEST_VERSION",
    "STATUS_OK",
    "STATUS_ERROR",
    "write_manifest",
    "read_manifest",
    "write_results_csv",
    "read_results_csv",
    "write_sweep_manifest",
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
) -> Path:
    """写单次运行的 manifest，返回文件路径。"""
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "status": status,
        "created_at": _now(),
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
