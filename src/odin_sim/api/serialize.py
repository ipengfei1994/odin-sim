"""JSON 序列化与结果表的列分类。

``aggregate`` 返回 numpy 标量与数组，序列化成 Python 原生类型；列分类只依据
**元信息列白名单**，其余一律视为指标列——这样换成任何模型前缀（``ref.*`` /
``g3.*``）都不用改代码。
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

__all__ = [
    "META_COLUMNS",
    "PARAM_COLUMNS",
    "jsonable",
    "result_columns",
    "split_columns",
]

META_COLUMNS = frozenset(
    {
        "run_id",
        "config_id",
        "traj_hash",
        "network_hash",
        "wall_seconds",
        "param_overrides",
        "status",
        "role",
        "control_run_id",
    }
)

PARAM_COLUMNS: tuple[str, ...] = (
    "model",
    "mode",
    "placement",
    "beta_gov",
    "tau",
    "scenario",
    "seed",
)


def jsonable(value: object) -> object:
    """把 numpy 标量与数组转成 JSON 能表达的原生类型。

    NaN / Inf 转成 ``null``：JSON 规范没有这两个字面量，若直接交给前端会
    得到 ``NaN`` 字串并把整张图拉歪——宁可缺失，不可失真。
    """
    if isinstance(value, np.ndarray):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, (np.floating,)):
        value = float(value)
    elif isinstance(value, (np.integer,)):
        return int(value)
    elif isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float):
        return None if not np.isfinite(value) else value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def result_columns(root: Path) -> list[str]:
    """读取 ``results.csv`` 的表头，不加载数据。"""
    path = root / "results.csv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as fh:
        header = fh.readline()
    return [c.strip() for c in header.rstrip("\r\n").split(",") if c.strip()]


def split_columns(columns: Sequence[str]) -> dict[str, list[str]]:
    """把表头分成参数列与指标列两类。"""
    params = [c for c in columns if c in PARAM_COLUMNS]
    metrics = [c for c in columns if c not in PARAM_COLUMNS and c not in META_COLUMNS]
    return {"params": params, "metrics": metrics}
