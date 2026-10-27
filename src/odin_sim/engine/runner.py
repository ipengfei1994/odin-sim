"""运行执行。

设计约束
--------
1. 引擎对逐步驱动不做假设：若模型声明 ``capabilities.stepwise`` 为
   ``False``，引擎只调用 ``run``；当前没有任何模型需要走逐步路径。
2. 计时写入产物，供后续性能基准使用，但**不作为正确性判据**。
3. 引擎不吞异常：模型失败必须向上冒泡，实验层自行决定是否跳过。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..model.protocol import DynamicsModel, RunOutput, RunRequest

__all__ = ["RunRecord", "run_once", "run_many"]


@dataclass
class RunRecord:
    """一次运行及其输入的可追溯记录。"""

    request: RunRequest
    output: RunOutput
    model_name: str
    capabilities: dict[str, bool] = field(default_factory=dict)

    @property
    def traj_hash(self) -> str:
        return self.output.traj_hash

    @property
    def network_hash(self) -> str:
        return self.output.network_hash

    def to_row(self) -> dict[str, Any]:
        """扁平化为一行（用于与 baseline CSV 比对）。"""
        return {
            "config_id": self.request.config_id,
            "seed": self.request.seed,
            "scenario": self.request.scenario,
            "model_name": self.model_name,
            "traj_hash": self.output.traj_hash,
            "network_hash": self.output.network_hash,
            "wall_seconds": self.output.wall_seconds,
        }


def run_once(model: DynamicsModel, request: RunRequest) -> RunRecord:
    """执行一次运行并计时。"""
    t0 = time.perf_counter()
    output = model.run(request)
    output.wall_seconds = time.perf_counter() - t0
    output.model_name = output.model_name or getattr(model, "name", "")

    caps = getattr(model, "capabilities", None)
    cap_dict: dict[str, bool] = {}
    if caps is not None:
        cap_dict = {
            "stepwise": bool(caps.stepwise),
            "multi_dim": bool(caps.multi_dim),
            "requires_control": bool(caps.requires_control),
        }
    return RunRecord(
        request=request,
        output=output,
        model_name=output.model_name,
        capabilities=cap_dict,
    )


def run_many(model: DynamicsModel, requests: Sequence[RunRequest]) -> list[RunRecord]:
    """顺序执行多组请求（顺序固定，保证行序可复现）。"""
    return [run_once(model, req) for req in requests]
