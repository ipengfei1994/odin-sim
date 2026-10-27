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
    """注册内建适配器。重复调用是幂等的（同名覆盖）。"""
    global _builtin_error
    try:
        from .adapters.g3 import register as _register_g3

        _register_g3()
    except Exception as exc:  # pragma: no cover - 取决于外部环境
        if _builtin_error is None:
            _builtin_error = f"{type(exc).__name__}: {exc}"
        return
    _builtin_error = None


def builtin_registration_error() -> str | None:
    """返回内建适配器注册失败的原因；成功则为 ``None``。"""
    return _builtin_error


register_builtins()
