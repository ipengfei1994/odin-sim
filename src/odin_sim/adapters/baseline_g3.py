"""运行期加载 G3 baseline 内核（位于 odin-agents 项目，只读接入）。

设计约束
--------
1. 不复制 baseline 源码进本仓库（避免代码分叉）。
2. 不修改 odin-agents 的任何文件；只把其 ``src`` 目录加入 ``sys.path`` 后 import。
3. 加载时校验模型版本，版本不符直接报错，不静默继续。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

EXPECTED_MODEL_VERSION = "g3-baseline-v1.0.0"
ENV_ROOT = "ODIN_BASELINE_ROOT"

__all__ = ["BaselineError", "BaselineKernel", "resolve_root", "load_kernel"]


class BaselineError(RuntimeError):
    """baseline 定位或加载失败。"""


@dataclass(frozen=True)
class BaselineKernel:
    """已加载的 baseline 内核句柄。"""

    root: Path
    model_version: str
    config_module: ModuleType
    model_module: ModuleType
    metrics_module: ModuleType
    topology_module: ModuleType
    runner_module: ModuleType
    Config: type
    Params: type
    scenario_params: Callable[..., Any]
    simulate: Callable[..., Any]
    build_network: Callable[..., Any]
    run_row: Callable[..., Any]
    all_configs: Callable[[], list]


def resolve_root(root: str | Path | None = None) -> Path:
    """定位 baseline 根目录（含 ``src/odin_g3``）。

    优先级：显式参数 > 环境变量 ``ODIN_BASELINE_ROOT``。找不到时抛出明确错误。
    """
    candidates: list[str | Path] = []
    if root:
        candidates.append(root)
    env = os.environ.get(ENV_ROOT)
    if env:
        candidates.append(env)

    for cand in candidates:
        p = Path(cand)
        if (p / "src" / "odin_g3" / "__init__.py").is_file():
            return p
        if (p / "odin_g3" / "__init__.py").is_file():
            return p.parent
        raise BaselineError(
            f"baseline 根目录无效：{p} 下未找到 src/odin_g3/__init__.py"
        )

    raise BaselineError(
        "未指定 baseline 根目录。请设置环境变量 "
        f"{ENV_ROOT}=<odin-agents/baseline> 或传入 --baseline-root 参数。"
    )


def load_kernel(root: str | Path | None = None) -> BaselineKernel:
    """加载 baseline 内核并校验模型版本。"""
    base = resolve_root(root)
    src = base / "src"
    src_str = str(src)

    already = sys.modules.get("odin_g3.config")
    if already is not None:
        loaded_from = getattr(already, "__file__", "")
        if loaded_from and not loaded_from.startswith(src_str):
            raise BaselineError(
                "odin_g3 已从其他路径加载，无法切换到目标 baseline："
                f"{loaded_from}"
            )

    inserted = False
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
        inserted = True

    try:
        from odin_g3 import config as config_module  # type: ignore[import-not-found]
        from odin_g3 import metrics as metrics_module  # type: ignore[import-not-found]
        from odin_g3 import model as model_module  # type: ignore[import-not-found]
        from odin_g3 import runner as runner_module  # type: ignore[import-not-found]
        from odin_g3 import topology as topology_module  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 依赖外部环境
        if inserted:
            sys.path.remove(src_str)
        raise BaselineError(f"导入 baseline 失败：{exc}") from exc

    version = getattr(config_module, "MODEL_VERSION", "unknown")
    if version != EXPECTED_MODEL_VERSION:
        raise BaselineError(
            f"baseline 模型版本不符：期望 {EXPECTED_MODEL_VERSION}，实际 {version}"
        )

    return BaselineKernel(
        root=base,
        model_version=version,
        config_module=config_module,
        model_module=model_module,
        metrics_module=metrics_module,
        topology_module=topology_module,
        runner_module=runner_module,
        Config=config_module.Config,
        Params=config_module.Params,
        scenario_params=config_module.scenario_params,
        simulate=model_module.simulate,
        build_network=topology_module.build_network,
        run_row=runner_module.run_row,
        all_configs=config_module.all_configs,
    )
