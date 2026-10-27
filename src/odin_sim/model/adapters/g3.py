"""G3 baseline 适配器（协议转换层，不含数值逻辑）。

本模块把外部 G3 baseline 内核（位置由 ``ODIN_BASELINE_ROOT`` 指定）包装成
本平台的协议实现：

* ``G3Dynamics``  —— 整段式动力学模型（``capabilities.stepwise = False``）
* ``G3Topology``  —— 拓扑生成（ba / er / ring，与内核同一实现）
* 四个指标       —— extreme / joint / backfire / alert+lead_time

**所有数值计算都委托给内核**，本模块不复制、不改写任何更新方程。
因此本模块的等价性不是"应当成立"，而是由回归测试逐位验证的事实
（见 ``tests/regression/test_g3_fingerprint.py``）。

已知限制
--------
1. ``G3Topology`` 目前只能被**外部调用**，不能注入进 ``G3Dynamics``：
   baseline 的 ``model.simulate`` 在模块作用域直接引用了自己的
   ``build_network``。要让平台提供 ws / sbm 等新拓扑，需要替换该引用，
   这属于 W2 的重构范围，不在本阶段做。
2. 逐步驱动（每步回调 agent）在 baseline 内核中不存在，故
   ``capabilities.stepwise`` 为 ``False``。引擎不得假设可以逐步驱动。
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ...adapters.baseline_g3 import BaselineKernel, load_kernel
from ..protocol import (
    DynamicsModel,
    Metric,
    MetricContext,
    ModelCapabilities,
    Network,
    NetworkSpec,
    RunOutput,
    RunRequest,
)
from ..registry import DYNAMICS, METRICS, TOPOLOGY

__all__ = [
    "G3Dynamics",
    "G3Topology",
    "G3ExtremeFinal",
    "G3JointFinal",
    "G3Backfire",
    "G3AlertLead",
    "get_kernel",
    "register",
]

DYNAMICS_NAME = "g3-baseline-v1"
SUPPORTED_TOPOLOGIES = ("ba", "er", "ring")

_kernel: BaselineKernel | None = None


def get_kernel(reload: bool = False) -> BaselineKernel:
    """获取（并缓存）baseline 内核句柄。"""
    global _kernel
    if _kernel is None or reload:
        _kernel = load_kernel()
    return _kernel


class G3Dynamics:
    """G3 baseline 动力学模型：整段式，输出完整轨迹。"""

    name = DYNAMICS_NAME
    capabilities = ModelCapabilities(
        stepwise=False,
        multi_dim=True,
        requires_control=True,
        deterministic_given_seed=True,
    )

    def run(self, request: RunRequest) -> RunOutput:
        kernel = get_kernel()
        cfg = kernel.Config(
            mode=request.mode,
            placement=request.placement,
            beta_gov=request.beta_gov,
            tau=request.tau,
        )
        params = kernel.scenario_params(request.scenario, **request.param_overrides)
        res = kernel.simulate(cfg, request.seed, params)
        params_payload = params.to_dict() if hasattr(params, "to_dict") else dict(params)
        return RunOutput(
            traj=res.traj,
            is_adv=res.is_adv,
            is_gov=res.is_gov,
            series_ord=res.series_ord,
            series_all=res.series_all,
            cost=res.cost,
            meta=res.meta,
            model_name=self.name,
            params=params_payload,
        )


class G3Topology:
    """G3 baseline 拓扑生成器（ba / er / ring）。"""

    name = DYNAMICS_NAME

    def build(self, spec: NetworkSpec) -> Network:
        if spec.kind not in SUPPORTED_TOPOLOGIES:
            raise ValueError(
                f"G3 baseline 不支持的拓扑：{spec.kind}；"
                f"当前仅支持 {SUPPORTED_TOPOLOGIES}"
            )
        kernel = get_kernel()
        net = kernel.build_network(
            spec.n,
            spec.seed,
            spec.kind,
            spec.options.get("m", 3),
            signs=spec.options.get("signs"),
            homophily=spec.options.get("homophily", 0.0),
        )
        return Network(
            src=net["src"],
            dst=net["dst"],
            deg=net["deg"],
            n_edges=net["n_edges"],
            hash=net["hash"],
            extra={"cross_edge_ratio": net.get("cross_edge_ratio", float("nan"))},
        )


class _G3MetricBase:
    """指标公共部分：持一份内核句柄，负责 ordinary 掩码。"""

    requires_control = False

    def __init__(self, kernel: BaselineKernel | None = None) -> None:
        self._kernel = kernel

    @property
    def kernel(self) -> BaselineKernel:
        if self._kernel is None:
            self._kernel = get_kernel()
        return self._kernel

    @staticmethod
    def _ordinary_mask(ctx: MetricContext) -> np.ndarray:
        return ~(ctx.is_adv | ctx.is_gov)


class G3ExtremeFinal(_G3MetricBase):
    """普通节点上敏感维极端占比的初值与末值。"""

    name = "g3.extreme_final_ord"

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        m = self.kernel.metrics_module
        tau_ext = float(ctx.params["tau_ext"])
        mask = self._ordinary_mask(ctx)
        E = m.series(ctx.traj, tau_ext, float(ctx.params["tau_joint"]), mask=mask)["extreme"]
        return {"extreme_t0_ord": float(E[0]), "extreme_final_ord": float(E[-1])}


class G3JointFinal(_G3MetricBase):
    """普通节点上多维联合极端占比的初值与末值。"""

    name = "g3.joint_final_ord"

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        m = self.kernel.metrics_module
        tau_ext = float(ctx.params["tau_ext"])
        tau_joint = float(ctx.params["tau_joint"])
        mask = self._ordinary_mask(ctx)
        J = m.series(ctx.traj, tau_ext, tau_joint, mask=mask)["joint"]
        return {"joint_t0_ord": float(J[0]), "joint_final_ord": float(J[-1])}


class G3Backfire(_G3MetricBase):
    """反噬判定（必须与同种子无治理对照配对）。"""

    name = "g3.backfire"
    requires_control = True

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        if ctx.control_traj is None:
            raise ValueError(
                "g3.backfire 需要 control_traj：反噬是与同种子无治理对照的配对量，"
                "缺失对照时不得退化为绝对值判定。"
            )
        m = self.kernel.metrics_module
        tau_ext = float(ctx.params["tau_ext"])
        tau_joint = float(ctx.params["tau_joint"])
        mask = self._ordinary_mask(ctx)
        E = m.series(ctx.traj, tau_ext, tau_joint, mask=mask)["extreme"]
        E_ctrl = m.series(ctx.control_traj, tau_ext, tau_joint, mask=mask)["extreme"]
        out = m.backfire(E, E_ctrl, float(ctx.params["delta_backfire"]))
        return {
            "bf_final": bool(out["bf_final"]),
            "bf_transient": bool(out["bf_transient"]),
            "bf_any": bool(out["bf_any"]),
            "excess_max": float(out["excess_max"]),
            "excess_t_max": int(out["excess_t_max"]),
        }


class G3AlertLead(_G3MetricBase):
    """联合极端序列上的预警步、事件步与提前量。"""

    name = "g3.alert_lead_time"

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        m = self.kernel.metrics_module
        tau_ext = float(ctx.params["tau_ext"])
        tau_joint = float(ctx.params["tau_joint"])
        mask = self._ordinary_mask(ctx)
        J = m.series(ctx.traj, tau_ext, tau_joint, mask=mask)["joint"]
        out = m.alert_and_event(J, float(ctx.params["tau_alert"]), float(ctx.params["tau_event"]))
        return {
            "alert_step": out["alert_step"],
            "event_step": out["event_step"],
            "lead_time": out["lead_time"],
            "censored": bool(out["censored"]),
        }


def register(*, overwrite: bool = True) -> None:
    """把 G3 适配器注册进全局注册表。

    baseline 不可用时**整体不注册**，而不是注册一半：G3 的每一个适配器
    最终都要落到 baseline 的模块上，半注册会让调用方以为模型可用。
    失败由 ``model.register_builtins`` 捕获并记录原因，不中断导入。

    默认允许覆盖：注册发生在导入期，重复导入同一模块是正常现象，
    且适配器对象无状态（内核句柄惰性加载）。
    """
    kernel = get_kernel()  # 失败即抛出，调用方负责降级

    kwargs = {"overwrite": overwrite}
    DYNAMICS.register(DYNAMICS_NAME, G3Dynamics(), **kwargs)
    TOPOLOGY.register(DYNAMICS_NAME, G3Topology(), **kwargs)
    METRICS.register(G3ExtremeFinal.name, G3ExtremeFinal(kernel), **kwargs)
    METRICS.register(G3JointFinal.name, G3JointFinal(kernel), **kwargs)
    METRICS.register(G3Backfire.name, G3Backfire(kernel), **kwargs)
    METRICS.register(G3AlertLead.name, G3AlertLead(kernel), **kwargs)


# 对外声明：这些类满足对应协议（由 tests/unit/test_model_protocol.py 断言）。
_ASSERT_PROTOCOL: tuple[type, ...] = (
    G3Dynamics,
    G3Topology,
    G3ExtremeFinal,
    G3JointFinal,
    G3Backfire,
    G3AlertLead,
)
