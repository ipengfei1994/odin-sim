"""S3｜并存式逐步驱动路径（不替换整段轨迹模型）。

并存，而非替换
--------------
既有 ``engine.run_once`` / ``G3Dynamics.run`` 保持原样：G3 的
``capabilities.stepwise=False``，本模块**明确拒绝**驱动它，绝不伪造逐步事件。
新的逐步模型（如 ``research.synthetic.SyntheticPopulation``）走本路径，
产生事件级数据；两条路径的产物互不覆盖，G3 指纹回归逐字符不受影响。

状态变更责任：模型在 ``step`` 内部修改自己的状态并产出 :class:`EventDraft`；
驱动层只负责分配事件 ID、校验顺序、落盘。事件本身不回写状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from .events import EventEnvelope, EventType, make_event
from .recorder import EventRecorder

__all__ = [
    "EventDraft", "StepwiseModel", "StepwiseResult", "StepwiseError",
    "supports_stepwise", "run_stepwise",
]


class StepwiseError(RuntimeError):
    """逐步驱动失败。"""


@dataclass(frozen=True)
class EventDraft:
    """模型产出的事件草案。``event_id`` 由驱动层内容寻址生成。"""

    event_type: EventType
    payload: Any
    agent_id: str | None = None
    label: str = ""
    """本事件的局部引用名，供后续事件的 causality_refs 解析。"""
    causal_labels: tuple[str, ...] = ()
    random_seed_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepwiseResult:
    simulation_id: str
    experiment_id: str
    n_events: int = 0
    n_ticks: int = 0
    event_digest: str = ""
    records: dict[str, list[Any]] = field(default_factory=dict)
    graphs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class StepwiseModel(Protocol):
    """可逐步驱动的研究模型协议。"""

    stepwise_capable: bool

    def initialize(self, context: Any) -> None:
        ...

    def step(self, tick: int) -> list[EventDraft]:
        ...

    def collect(self) -> tuple[dict[str, list[Any]], dict[str, Any]]:
        """返回 (records, graphs)。"""
        ...


def supports_stepwise(model: Any) -> bool:
    """严格判定：只认显式声明，不从方法存在与否推断。"""
    if bool(getattr(model, "stepwise_capable", False)):
        return True
    caps = getattr(model, "capabilities", None)
    return bool(caps is not None and getattr(caps, "stepwise", False))


def run_stepwise(
    model: Any,
    *,
    config: Any,
    simulation_id: str,
    experiment_id: str,
    recorder: EventRecorder,
    steps: int | None = None,
) -> StepwiseResult:
    """驱动一次逐步仿真并实时落盘事件。

    参数
    ----
    config:
        ``ExperimentConfig``（或其 ``with_overrides`` 派生）。
    steps:
        覆盖 ``config.simulation_steps``；逐步模型用于 N 序列扫描时按 N 调整。
    """
    if not supports_stepwise(model):
        raise StepwiseError(
            "模型未声明 stepwise 能力，拒绝用逐步路径驱动。"
            "既有 G3 baseline 为整段轨迹型，必须走核心 run_once；"
            "不得为其伪造事件（T-DATA/1–4）"
        )
    n_steps = int(steps if steps is not None else config.simulation_steps)
    context = {
        "config": config,
        "simulation_id": simulation_id,
        "experiment_id": experiment_id,
        "steps": n_steps,
    }
    model.initialize(context)

    result = StepwiseResult(simulation_id=simulation_id, experiment_id=experiment_id)
    label_to_id: dict[str, str] = {}
    seq = 0
    for tick in range(n_steps):
        drafts: Sequence[EventDraft] = model.step(tick)
        for draft in drafts:
            refs = []
            for label in draft.causal_labels:
                if label not in label_to_id:
                    raise StepwiseError(f"事件引用了不存在的上游 label：{label}")
                refs.append(label_to_id[label])
            event = make_event(
                draft.event_type,
                simulation_id=simulation_id,
                experiment_id=experiment_id,
                tick=tick,
                sequence_no=seq,
                payload=draft.payload,
                agent_id=draft.agent_id,
                random_seed_context=draft.random_seed_context,
                causality_refs=refs,
            )
            recorder.append(event)
            seq += 1
            if draft.label:
                label_to_id[draft.label] = event.event_id
        result.n_ticks = tick + 1

    result.records, result.graphs = model.collect()
    result.n_events = recorder.count
    result.event_digest = recorder.digest
    return result
