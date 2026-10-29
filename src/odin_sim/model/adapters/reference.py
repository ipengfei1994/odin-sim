"""内置参考动力学模型（``reference-v1``）：纯 Python、确定性、零外部依赖。

设计定位
--------
* 作为 G3 baseline 内核的 **stand-in**：实现同一份 ``DynamicsModel`` 协议与
  ``RunOutput`` 数据形状，但不包含任何真实物理。它让平台（实验编排 / 聚合 /
  时序 / UI / CLI）在**没有 odin_g3 内核**的环境下也能端到端跑通、可测、可演示。
* 永远可用：不依赖外部内核，注册于 ``register_builtins()``，与 G3 并列；
  G3 就绪后只需在配置里把 ``model`` 从 ``reference-v1`` 改为 ``g3-baseline-v1``
  即可切换，平台代码无需改动。
* **注意**：参考模型的数值是合成占位，**不能当作科学结论**；真实物理等价性由
  G3 的指纹回归（``tests/regression``）保证，与本项目无关。

数值设计（刻意简单、确定、对治理参数有响应）
------------------------------------------------
* N 个 agent、D=2 维意见，取值 [0, 1]。
* ``polarized`` 场景：两簇（中心 (0.1,0.1) 与 (0.9,0.9)）；``neutral``：围绕 (0.5,0.5)。
* 每步：朝本簇中心轻微聚合（w_base）+ 治理开启后（t>=tau）朝中庸 (0.5,0.5)
  按 beta_gov 强度拉拢 + 微噪声。beta_gov 越大、tau 越早，极端占比下降越明显——
  使 explore/timeseries 在扫描 beta_gov 时呈现有意义的差异。
* 比例量（extreme / joint / mean_opinion）均落在 [0,1]，分析层按比例量裁剪。
"""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from ..protocol import (
    DynamicsModel,
    Metric,
    MetricContext,
    ModelCapabilities,
    RunOutput,
    RunRequest,
)
from ..registry import DYNAMICS, METRICS

__all__ = ["REFERENCE_NAME", "ReferenceDynamics", "register"]

REFERENCE_NAME = "reference-v1"

# 默认阈值（与 G3 语义对齐的占位值；指标从 RunOutput.params 读取，缺失时回退到此）。
DEFAULT_PARAMS: dict[str, float] = {
    "tau_ext": 0.8,
    "tau_joint": 0.8,
    "delta_backfire": 0.1,
    "tau_alert": 0.5,
    "tau_event": 0.6,
}

_N_AGENTS = 200
_N_DIMS = 2
_N_STEPS = 151  # 与 G3 轨迹长度一致，保证 timeseries.csv 同为 151 点


def _extreme_flag(opinion: np.ndarray, tau_ext: float) -> np.ndarray:
    """节点是否极端：任一维 > tau_ext 或 < 1 - tau_ext。"""
    return np.any(opinion > tau_ext, axis=-1) | np.any(opinion < (1.0 - tau_ext), axis=-1)


def _joint_flag(opinion: np.ndarray, tau_joint: float) -> np.ndarray:
    """节点是否联合极端：两维同时极端。"""
    both = (opinion > tau_joint) | (opinion < (1.0 - tau_joint))
    return np.all(both, axis=-1)


class ReferenceDynamics:
    """纯 Python 合成动力学：实现 ``DynamicsModel`` 协议。"""

    name = REFERENCE_NAME
    capabilities = ModelCapabilities(
        stepwise=False,
        multi_dim=True,
        requires_control=True,  # backfire 指标需要同种子对照
        deterministic_given_seed=True,
    )

    def run(self, request: RunRequest) -> RunOutput:
        rng = np.random.default_rng(request.seed)
        n, d, t = _N_AGENTS, _N_DIMS, _N_STEPS

        # 初始化：polarized 两簇；其余（含 neutral）围绕中庸。
        if request.scenario == "polarized":
            centers = np.array([[0.1, 0.1], [0.9, 0.9]])
            assign = rng.integers(0, 2, size=n)
            centers_agent = centers[assign]
            op = centers_agent + rng.normal(0.0, 0.05, size=(n, d))
        else:
            centers_agent = np.full((n, d), 0.5)
            op = rng.normal(0.5, 0.08, size=(n, d))
        op = np.clip(op, 0.0, 1.0)

        # 角色掩码
        is_adv = rng.random(n) < 0.08
        is_gov = rng.random(n) < 0.05
        ordinary = ~(is_adv | is_gov)

        tau = int(request.tau)
        beta = float(request.beta_gov)
        w_base = 0.02

        traj = np.empty((t, n, d), dtype=float)
        traj[0] = op
        for step in range(1, t):
            op = op + w_base * (centers_agent - op)  # 向本簇中心聚合
            if step >= tau:
                op = op + beta * (0.5 - op)  # 治理开启：朝中庸拉拢
            op = op + rng.normal(0.0, 0.005, size=(n, d))
            op = np.clip(op, 0.0, 1.0)
            traj[step] = op

        te = float(DEFAULT_PARAMS["tau_ext"])
        tj = float(DEFAULT_PARAMS["tau_joint"])
        series_ord = self._series(traj, ordinary, te, tj)
        series_all = self._series(traj, np.ones(n, dtype=bool), te, tj)

        params = dict(DEFAULT_PARAMS)
        params.update(
            scenario=request.scenario,
            mode=request.mode,
            beta_gov=beta,
            tau=tau,
        )

        meta = {
            "traj_hash": hashlib.sha1(traj.tobytes()).hexdigest()[:16],
            "network_hash": f"reference-{request.seed}",
        }
        return RunOutput(
            traj=traj,
            is_adv=is_adv,
            is_gov=is_gov,
            series_ord=series_ord,
            series_all=series_all,
            cost={"n_agents": n, "steps": t, "n_dims": d},
            meta=meta,
            model_name=self.name,
            params=params,
        )

    @staticmethod
    def _series(
        traj: np.ndarray,
        mask: np.ndarray,
        tau_ext: float,
        tau_joint: float,
    ) -> dict[str, np.ndarray]:
        seq = traj[:, mask, :]  # (T, M, D)
        extreme = _extreme_flag(seq, tau_ext).mean(axis=1)  # (T,)
        joint = _joint_flag(seq, tau_joint).mean(axis=1)  # (T,)
        mean_op = seq.mean(axis=(1, 2))  # (T,)
        return {
            "extreme": extreme.astype(float),
            "joint": joint.astype(float),
            "mean_opinion": mean_op.astype(float),
        }


# --------------------------------------------------------------------------- #
# 参考指标（纯 Python，不依赖 odin_g3；命名空间 ref.*，与 g3.* 并列）
# --------------------------------------------------------------------------- #
class _RefMetricBase:
    requires_control = False

    @staticmethod
    def _ordinary_mask(ctx: MetricContext) -> np.ndarray:
        return ~(ctx.is_adv | ctx.is_gov)


class RefExtremeFinal(_RefMetricBase):
    """普通节点上极端占比的初值与末值。"""

    name = "ref.extreme_final_ord"

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        tau_ext = float(ctx.params.get("tau_ext", DEFAULT_PARAMS["tau_ext"]))
        mask = self._ordinary_mask(ctx)
        e0 = _extreme_flag(ctx.traj[0], tau_ext)[mask].mean()
        e1 = _extreme_flag(ctx.traj[-1], tau_ext)[mask].mean()
        return {
            "extreme_t0_ord": float(e0),
            "extreme_final_ord": float(e1),
        }


class RefJointFinal(_RefMetricBase):
    """普通节点上多维联合极端占比的初值与末值。"""

    name = "ref.joint_final_ord"

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        tau_joint = float(ctx.params.get("tau_joint", DEFAULT_PARAMS["tau_joint"]))
        mask = self._ordinary_mask(ctx)
        j0 = _joint_flag(ctx.traj[0], tau_joint)[mask].mean()
        j1 = _joint_flag(ctx.traj[-1], tau_joint)[mask].mean()
        return {
            "joint_t0_ord": float(j0),
            "joint_final_ord": float(j1),
        }


class RefBackfire(_RefMetricBase):
    """反噬判定（必须与同种子无治理对照配对）。"""

    name = "ref.backfire"
    requires_control = True

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        if ctx.control_traj is None:
            raise ValueError(
                "ref.backfire 需要 control_traj：反噬是与同种子无治理对照的配对量，"
                "缺失对照时不得退化为绝对值判定。"
            )
        tau_ext = float(ctx.params.get("tau_ext", DEFAULT_PARAMS["tau_ext"]))
        delta = float(ctx.params.get("delta_backfire", DEFAULT_PARAMS["delta_backfire"]))
        mask = self._ordinary_mask(ctx)
        e_main = _extreme_flag(ctx.traj[-1], tau_ext)[mask].mean()
        e_ctrl = _extreme_flag(ctx.control_traj[-1], tau_ext)[mask].mean()
        excess = float(e_main - e_ctrl)
        return {
            "bf_final": bool(excess > delta),
            "bf_any": bool(excess > 0),
            "excess_max": excess,
            "excess_t_max": 0,
        }


class RefAlertLead(_RefMetricBase):
    """联合极端序列上的预警步、事件步与提前量。"""

    name = "ref.alert_lead_time"

    def compute(self, ctx: MetricContext) -> dict[str, Any]:
        tau_joint = float(ctx.params.get("tau_joint", DEFAULT_PARAMS["tau_joint"]))
        tau_alert = float(ctx.params.get("tau_alert", DEFAULT_PARAMS["tau_alert"]))
        tau_event = float(ctx.params.get("tau_event", DEFAULT_PARAMS["tau_event"]))
        mask = self._ordinary_mask(ctx)
        joint = _joint_flag(ctx.traj, tau_joint)[:, mask].mean(axis=1)  # (T,)
        alert_step = int(np.argmax(joint > tau_alert)) if np.any(joint > tau_alert) else -1
        event_step = int(np.argmax(joint > tau_event)) if np.any(joint > tau_event) else -1
        lead = (event_step - alert_step) if (alert_step >= 0 and event_step >= 0) else -1
        return {
            "alert_step": alert_step,
            "event_step": event_step,
            "lead_time": int(lead),
            "censored": bool(event_step < 0),
        }


def register(*, overwrite: bool = True) -> None:
    """把参考模型与参考指标注册进全局注册表（永远可用，不依赖内核）。"""
    kwargs = {"overwrite": overwrite}
    DYNAMICS.register(REFERENCE_NAME, ReferenceDynamics(), **kwargs)
    METRICS.register(RefExtremeFinal.name, RefExtremeFinal(), **kwargs)
    METRICS.register(RefJointFinal.name, RefJointFinal(), **kwargs)
    METRICS.register(RefBackfire.name, RefBackfire(), **kwargs)
    METRICS.register(RefAlertLead.name, RefAlertLead(), **kwargs)
