"""模型层：协议 + 注册表 + 适配器。

导入本包会注册所有内建适配器（当前只有 G3 baseline 一个）。注册失败
（baseline 不存在或版本不符）**不会**中断导入——平台在没有 baseline 的
环境下仍应可用，只是该模型不可用。
"""
from __future__ import annotations

from .protocol import (  # noqa: F401
    DynamicsModel,
    Metric,
    MetricContext,
    ModelCapabilities,
    Network,
    NetworkSpec,
    RunOutput,
    RunRequest,
    TopologyModel,
)
from .registry import DYNAMICS, METRICS, TOPOLOGY, Registry, RegistryError  # noqa: F401

__all__ = [
    "DYNAMICS",
    "METRICS",
    "TOPOLOGY",
    "Registry",
    "RegistryError",
    "DynamicsModel",
    "Metric",
    "MetricContext",
    "ModelCapabilities",
    "Network",
    "NetworkSpec",
    "RunOutput",
    "RunRequest",
    "TopologyModel",
    "register_builtins",
    "builtin_registration_error",
]

_builtin_error: str | None = None


def register_builtins() -> None:
    """注册内建实现。重复调用是幂等的（同名覆盖）。

    平台原生的拓扑生成器**不依赖 baseline**，先注册；G3 适配器依赖
    baseline，失败时只记录原因，不影响前面已注册的部分。
    """
    global _builtin_error
    from ..topology.model import TOPOLOGY_NAME, PlatformTopology

    TOPOLOGY.register(TOPOLOGY_NAME, PlatformTopology(), overwrite=True)

    g3_error: str | None = None
    try:
        from .adapters.g3 import register as _register_g3

        _register_g3()
    except Exception as exc:  # pragma: no cover - 取决于外部环境
        g3_error = f"{type(exc).__name__}: {exc}"

    # 参考模型不依赖内核，始终注册：作为内置 CI / 测试 / 离线演示模型，
    # 让平台在没有 odin_g3 时也能端到端跑通。它若失败属平台自身 bug，直接抛出。
    from .adapters.reference import register as _register_ref

    _register_ref()

    # _builtin_error 仅反映 G3 内核可用性；参考模型失败不掩盖该信息。
    _builtin_error = g3_error


def builtin_registration_error() -> str | None:
    """返回内建适配器注册失败的原因；成功则为 ``None``。"""
    return _builtin_error


register_builtins()
