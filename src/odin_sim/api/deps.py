"""服务层依赖：产物根目录与 sweep 定位。

``sweep_id`` 来自 URL，必须约束在产物根目录之内，否则 ``../../`` 形式的路径
穿越会把产物目录之外的文件暴露出去。
"""
from __future__ import annotations

import os
from pathlib import Path

from ..storage.layout import results_csv_path

__all__ = ["default_results_root", "resolve_results_root", "resolve_sweep"]

RESULTS_ROOT_ENV = "ODIN_RESULTS_ROOT"


def default_results_root() -> Path:
    """默认产物根目录：环境变量优先，否则取当前工作目录下的 ``results/``。

    之所以优先环境变量：用户的工作目录未必是仓库根（Jupyter、定时任务、
    系统服务等都有可能），显式指定比"猜"可靠。
    """
    env = os.environ.get(RESULTS_ROOT_ENV)
    if env:
        return Path(env)
    return Path.cwd() / "results"


def resolve_results_root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else default_results_root()


def resolve_sweep(root: Path, sweep_id: str) -> Path | None:
    """把 ``sweep_id`` 解析成目录；越界或不存在一律返回 ``None``。

    判定条件是它与根目录的**相对路径**合法且确实包含 ``results.csv``，
    而不是字符串长得像不像——后者容易被 ``..`` 绕过。
    """
    if not sweep_id or sweep_id in (".", ".."):
        return None
    candidate = (root / sweep_id).resolve()
    base = root.resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if results_csv_path(candidate).is_file():
        return candidate
    return None
