"""Sweep 产物读取与聚合端点。

所有数值都经 ``analysis`` 的公开函数产出，**不在本模块做任何统计计算**——
页面上的每个点和每条置信带，CLI 都能用同样的参数复算出来。

``aggregate`` 的行里带 ``<metric>.n``，这是前端"敢不敢画置信带"的唯一依据：
``n >= 2`` 画带；``n == 1`` 只画线并标注不可用于推断；``n == 0`` 说明该指标
在这次 sweep 里根本没被计算（通常是缺配对对照），直接显示原因而非画空图。
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..analysis import aggregate, aggregate_timeseries, filter_by_role, load_results, load_run_series
from ..storage.layout import sweep_manifest_path
from . import serialize
from .deps import resolve_sweep

router = APIRouter(prefix="/api", tags=["sweeps"])

_LIST_LIMIT_MAX = 2000


def _root(request: Request):
    return request.app.state.results_root


def _sweep_dir(request: Request, sweep_id: str):
    root = _root(request)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"产物根目录不存在：{root}")
    path = resolve_sweep(root, sweep_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"未找到 sweep：{sweep_id}")
    return path


def _read_manifest(sweep_dir) -> dict[str, Any]:
    path = sweep_manifest_path(sweep_dir)
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _default_group_by(columns: Sequence[str]) -> list[str]:
    """默认分组键：``beta_gov`` 若存在则用它，否则取第一个参数列。"""
    if "beta_gov" in columns:
        return ["beta_gov"]
    params = serialize.split_columns(columns)["params"]
    return [params[0]] if params else ["model"]


@router.get("/sweeps")
def api_list_sweeps(request: Request) -> dict:
    """列出产物根目录下的所有 sweep（每个含一次 ``results.csv``）。"""
    root = _root(request)
    if not root.is_dir():
        return {"root": str(root), "exists": False, "sweeps": []}

    items = []
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (child / "results.csv").is_file():
            continue
        manifest = _read_manifest(child)
        stat = child.stat()
        columns = serialize.result_columns(child)
        classified = serialize.split_columns(columns)
        items.append(
            {
                "id": child.name,
                "name": manifest.get("name") or child.name,
                "model": manifest.get("spec", {}).get("model") or "—",
                "fingerprint": manifest.get("fingerprint") or "—",
                "counts": manifest.get("counts") or {},
                "skipped_metrics": manifest.get("skipped_metrics") or [],
                "params": classified["params"],
                "metrics": classified["metrics"],
                "updated": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return {"root": str(root), "exists": True, "sweeps": items}


@router.get("/sweeps/{sweep_id}")
def api_sweep_detail(sweep_id: str, request: Request) -> dict:
    """单个 sweep 的规格、统计与被跳过指标的原因。"""
    sweep_dir = _sweep_dir(request, sweep_id)
    manifest = _read_manifest(sweep_dir)
    columns = serialize.result_columns(sweep_dir)
    return serialize.jsonable(
        {
            "id": sweep_id,
            "manifest": manifest,
            "columns": serialize.split_columns(columns),
            "default_group_by": _default_group_by(columns),
        }
    )


@router.get("/sweeps/{sweep_id}/runs")
def api_sweep_runs(
    sweep_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=_LIST_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """原始运行记录（分页）。对照行默认不混入主运行列表。"""
    sweep_dir = _sweep_dir(request, sweep_id)
    rows = load_results(sweep_dir)
    window = rows[offset : offset + limit]
    return serialize.jsonable(
        {
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "rows": window,
            "columns": serialize.result_columns(sweep_dir),
        }
    )


@router.get("/sweeps/{sweep_id}/aggregate")
def api_sweep_aggregate(
    sweep_id: str,
    request: Request,
    group_by: list[str] = Query(default=None),
    metrics: list[str] = Query(default=None),
    confidence: float = Query(default=0.95, gt=0.0, lt=1.0),
    role: str = Query(default="main"),
) -> dict:
    """跨条件聚合：返回每组的均值、标准差与置信区间，**含样本量 n**。"""
    sweep_dir = _sweep_dir(request, sweep_id)
    rows = load_results(sweep_dir)
    if role != "all":
        rows = filter_by_role(rows, role)
    columns = serialize.result_columns(sweep_dir)
    classified = serialize.split_columns(columns)

    keys = list(group_by) if group_by else _default_group_by(columns)
    unknown = [k for k in keys if k not in columns]
    if unknown:
        raise HTTPException(status_code=422, detail=f"分组键不存在于结果表：{unknown}")

    targets = list(metrics) if metrics else classified["metrics"]
    result = aggregate(rows, group_by=keys, metrics=targets, confidence=confidence)
    return serialize.jsonable(
        {
            "group_by": keys,
            "metrics": targets,
            "confidence": confidence,
            "role": role,
            "n_rows": len(rows),
            "rows": result,
        }
    )


@router.get("/sweeps/{sweep_id}/curves")
def api_sweep_curves(
    sweep_id: str,
    request: Request,
    group_by: list[str] = Query(default=None),
    columns: list[str] = Query(default=None),
    confidence: float = Query(default=0.95, gt=0.0, lt=1.0),
    role: str = Query(default="main"),
) -> dict:
    """演化曲线：跨种子逐时间步聚合，返回逐点的均值与置信带。"""
    sweep_dir = _sweep_dir(request, sweep_id)
    sweep_columns = serialize.result_columns(sweep_dir)
    keys = list(group_by) if group_by else _default_group_by(sweep_columns)

    records = load_run_series(sweep_dir, role=role, spec_keys=keys)
    if not records:
        return {
            "group_by": keys,
            "confidence": confidence,
            "available": False,
            "reason": "本次 sweep 没有可用的 timeseries.csv（多为 store_trajectory 未开或运行失败）",
            "series": [],
        }

    targets = list(columns) if columns else None
    try:
        series = aggregate_timeseries(
            records, group_by=keys, columns=targets, confidence=confidence
        )
    except ValueError as exc:  # 组内时间轴不对齐是数据问题，不能静默平均
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return serialize.jsonable(
        {
            "group_by": keys,
            "confidence": confidence,
            "available": True,
            "n_runs": len(records),
            "series": series,
        }
    )
