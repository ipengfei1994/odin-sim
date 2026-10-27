"""平台内核协议（W1-c）。

本模块只定义**接口与数据形状**，不包含任何具体模型的实现，也不引入
baseline 的语义。所有模型通过 ``model/registry.py`` 注册后被引擎调用。

设计前提
--------
1. 协议不得为了"看起来完整"而声明当前无人实现的能力。没有实现的能力
   一律进入 ``ModelCapabilities`` 的布尔标志，由调用方在运行时检查。
2. ``run`` 是唯一必需能力；``step`` 是可选能力。G3 baseline 目前只提供
   整段式轨迹，因此其 ``capabilities.stepwise`` 为 ``False``，引擎不得
   假设可以逐步驱动它。
3. 协议不规定意见维度的含义、更新方程或指标口径——这些属于模型自身的
   责任，由模型注册时自带的元数据声明。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

__all__ = [
    "ModelCapabilities",
    "RunRequest",
    "RunOutput",
    "NetworkSpec",
    "Network",
    "MetricContext",
    "DynamicsModel",
    "TopologyModel",
    "Metric",
]


@dataclass(frozen=True)
class ModelCapabilities:
    """模型能力声明。未实现的能力必须为 ``False``，不得靠约定俗成推断。"""

    stepwise: bool = False
    """是否支持逐步驱动（initialize/step/terminate）。"""

    multi_dim: bool = False
    """意见是否为多维（>1 维）。"""

    requires_control: bool = False
    """指标是否需要与同种子无治理对照配对计算。"""

    deterministic_given_seed: bool = True
    """同 (config, seed, params) 是否保证逐位可复现。"""


@dataclass(frozen=True)
class RunRequest:
    """一次运行的完整输入。不含任何隐式状态或全局随机源。"""

    mode: str
    seed: int
    placement: str = "ba"
    beta_gov: float = 0.0
    tau: int = 0
    scenario: str = "polarized"
    param_overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def config_id(self) -> str:
        """与 baseline 一致的命名约定：``{mode}_{placement}_b{beta:03d}_t{tau:02d}``。"""
        b = int(round(self.beta_gov * 1000))
        return f"{self.mode}_{self.placement}_b{b:03d}_t{self.tau:02d}"


@dataclass
class RunOutput:
    """一次运行的产物。字段与 baseline 的 ``SimResult`` 对齐，另加运行计量。"""

    traj: np.ndarray
    is_adv: np.ndarray
    is_gov: np.ndarray
    series_ord: dict[str, np.ndarray]
    series_all: dict[str, np.ndarray]
    cost: dict[str, Any]
    meta: dict[str, Any]
    model_name: str = ""
    wall_seconds: float = 0.0

    @property
    def traj_hash(self) -> str:
        return str(self.meta.get("traj_hash", ""))

    @property
    def network_hash(self) -> str:
        return str(self.meta.get("network_hash", ""))


@dataclass(frozen=True)
class NetworkSpec:
    """拓扑生成请求。参数含义由具体拓扑实现自己声明。"""

    kind: str
    n: int
    seed: int
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Network:
    """无向网络的边表表示（COO，双向）。"""

    src: np.ndarray
    dst: np.ndarray
    deg: np.ndarray
    n_edges: int
    hash: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricContext:
    """指标计算的输入。只包含轨迹与参数，指标不得回写状态。"""

    traj: np.ndarray
    is_adv: np.ndarray
    is_gov: np.ndarray
    params: dict[str, Any]
    control_traj: np.ndarray | None = None


@runtime_checkable
class DynamicsModel(Protocol):
    """动力学模型：给定请求，产出一条完整轨迹及其派生量。"""

    name: str
    capabilities: ModelCapabilities

    def run(self, request: RunRequest) -> RunOutput:
        """执行一次完整运行。"""
        ...


@runtime_checkable
class TopologyModel(Protocol):
    """拓扑生成器：给定规格，产出一张图。"""

    name: str

    def build(self, spec: NetworkSpec) -> Network:
        ...


@runtime_checkable
class Metric(Protocol):
    """指标：给定轨迹上下文，产出扁平标量字典。"""

    name: str
    requires_control: bool

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        ...
