"""S2｜强类型事件体系（event-schema.md 的 12 类事件）。

设计纪律
--------
* **不做无类型约束的通用 JSON 字典**：每个 ``EventType`` 绑定唯一 payload
  dataclass，工厂在构造时校验类型与必填字段。
* **状态变更责任**：事件只描述"发生了什么"，不回写状态；状态的唯一写入方
  是 ``synthetic.SyntheticPopulation`` 的步进逻辑，Recorder 只负责落盘。
* **引用不可变**：``causality_refs`` 指向上游 event_id；event_id 由内容寻址
  生成，重放两次必须逐字符一致。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

__all__ = [
    "EventType", "EventEnvelope", "EVENT_PAYLOAD_TYPES", "EVENT_AGENT_REQUIRED",
    "EVENT_PHASE", "EventError", "make_event", "validate_order",
    "ContentCreated", "ContentPublished", "ContentExposed", "ExposureSelected",
    "InteractionCreated", "AgentDecisionMade", "AgentStateChanged",
    "OpinionChanged", "NetworkEdgeAdded", "NetworkEdgeRemoved",
    "InterventionApplied", "MetricRecorded",
]


class EventError(ValueError):
    """事件构造或校验失败。"""


class EventType(str, Enum):
    CONTENT_CREATED = "ContentCreated"
    CONTENT_PUBLISHED = "ContentPublished"
    CONTENT_EXPOSED = "ContentExposed"
    EXPOSURE_SELECTED = "ExposureSelected"
    INTERACTION_CREATED = "InteractionCreated"
    AGENT_DECISION_MADE = "AgentDecisionMade"
    AGENT_STATE_CHANGED = "AgentStateChanged"
    OPINION_CHANGED = "OpinionChanged"
    NETWORK_EDGE_ADDED = "NetworkEdgeAdded"
    NETWORK_EDGE_REMOVED = "NetworkEdgeRemoved"
    INTERVENTION_APPLIED = "InterventionApplied"
    METRIC_RECORDED = "MetricRecorded"


# ---------------------------------------------------------------------------
# 12 类强类型 payload
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InterventionApplied:
    intervention_id: str
    gov_omega: int = 0
    gov_tau: str = ""
    gov_kappa: str = ""
    target_agent_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentDecisionMade:
    decision_id: str
    intent_action: str
    execution_status: str = "pending"
    exposure_ref: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ContentCreated:
    content_id: str
    text_hash: str = ""
    topic: str = ""


@dataclass(frozen=True)
class ContentPublished:
    content_id: str
    distribution: str = ""
    candidate_count: int = 0
    rec_alpha: float | None = None
    """推荐个性化强度（Q-31：带 rec_ 前缀，不与 G3 alpha 混用）。"""


@dataclass(frozen=True)
class ContentExposed:
    exposure_id: str
    content_id: str
    source_agent_id: str = ""
    visible_seconds: float = 0.0
    in_viewport: bool = False
    candidate_set_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExposureSelected:
    exposure_id: str
    content_id: str
    selected: bool = True
    selection_mode: str = "voluntary"
    """voluntary / forced；选择过程必须实时记录，事后无法重建。"""


@dataclass(frozen=True)
class InteractionCreated:
    interaction_id: str
    action_type: str
    content_id: str = ""
    target_agent_id: str = ""
    decision_ref: str = ""
    executed: bool = True
    rejection_reason: str = ""


@dataclass(frozen=True)
class OpinionChanged:
    transition_id: str
    before: tuple[float, ...] = ()
    after: tuple[float, ...] = ()
    cause_exposure_id: str = ""
    mechanism: str = ""
    causal_edge_kind: str = "known_causal"


@dataclass(frozen=True)
class AgentStateChanged:
    transition_id: str
    field_name: str = ""
    before: Any = None
    after: Any = None


@dataclass(frozen=True)
class NetworkEdgeAdded:
    network_type: str
    src: str = ""
    dst: str = ""
    weight: float = 1.0
    relation_kind: str = ""
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class NetworkEdgeRemoved:
    network_type: str
    src: str = ""
    dst: str = ""
    reason: str = ""


@dataclass(frozen=True)
class MetricRecorded:
    metric_id: str
    value: float | None = None
    status: str = "recorded"
    """recorded / skipped；skipped 时 value 必须为 None 且给 reason。"""
    reason: str = ""
    definition_version: str = ""


EVENT_PAYLOAD_TYPES: dict[EventType, type] = {
    EventType.INTERVENTION_APPLIED: InterventionApplied,
    EventType.AGENT_DECISION_MADE: AgentDecisionMade,
    EventType.CONTENT_CREATED: ContentCreated,
    EventType.CONTENT_PUBLISHED: ContentPublished,
    EventType.CONTENT_EXPOSED: ContentExposed,
    EventType.EXPOSURE_SELECTED: ExposureSelected,
    EventType.INTERACTION_CREATED: InteractionCreated,
    EventType.OPINION_CHANGED: OpinionChanged,
    EventType.AGENT_STATE_CHANGED: AgentStateChanged,
    EventType.NETWORK_EDGE_ADDED: NetworkEdgeAdded,
    EventType.NETWORK_EDGE_REMOVED: NetworkEdgeRemoved,
    EventType.METRIC_RECORDED: MetricRecorded,
}

EVENT_AGENT_REQUIRED: frozenset[EventType] = frozenset({
    EventType.AGENT_DECISION_MADE, EventType.CONTENT_CREATED,
    EventType.CONTENT_EXPOSED, EventType.EXPOSURE_SELECTED,
    EventType.INTERACTION_CREATED, EventType.OPINION_CHANGED,
    EventType.AGENT_STATE_CHANGED,
})

# 同一 tick 内的发生顺序（数值小者先发生）；跨 tick 重置。
EVENT_PHASE: dict[EventType, int] = {
    EventType.INTERVENTION_APPLIED: 0,
    EventType.AGENT_DECISION_MADE: 1,
    EventType.CONTENT_CREATED: 2,
    EventType.CONTENT_PUBLISHED: 3,
    EventType.CONTENT_EXPOSED: 4,
    EventType.EXPOSURE_SELECTED: 5,
    EventType.INTERACTION_CREATED: 6,
    EventType.OPINION_CHANGED: 7,
    EventType.AGENT_STATE_CHANGED: 7,
    EventType.NETWORK_EDGE_ADDED: 8,
    EventType.NETWORK_EDGE_REMOVED: 8,
    EventType.METRIC_RECORDED: 9,
}


@dataclass(frozen=True)
class EventEnvelope:
    """所有 12 类事件共有的统一信封。"""

    event_id: str
    event_type: str
    simulation_id: str
    experiment_id: str
    tick: int
    sequence_no: int
    payload: dict[str, Any]
    agent_id: str | None = None
    timestamp: str = ""
    random_seed_context: dict[str, Any] = field(default_factory=dict)
    causality_refs: tuple[str, ...] = ()
    schema_version: str = "odin-research-event-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventEnvelope":
        try:
            return cls(
                event_id=str(data["event_id"]),
                event_type=str(data["event_type"]),
                simulation_id=str(data["simulation_id"]),
                experiment_id=str(data["experiment_id"]),
                tick=int(data["tick"]),
                sequence_no=int(data["sequence_no"]),
                payload=dict(data["payload"]),
                agent_id=data.get("agent_id"),
                timestamp=str(data.get("timestamp", "")),
                random_seed_context=dict(data.get("random_seed_context") or {}),
                causality_refs=tuple(data.get("causality_refs") or ()),
                schema_version=str(data.get("schema_version", "odin-research-event-v1")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EventError(f"事件信封反序列化失败：{exc}") from exc

    @property
    def typed_payload(self) -> Any:
        """还原为强类型 payload（用于回放后的类型检查）。"""
        et = EventType(self.event_type)
        return EVENT_PAYLOAD_TYPES[et](**self.payload)


def _canonical(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def make_event(
    event_type: EventType | str,
    *,
    simulation_id: str,
    experiment_id: str,
    tick: int,
    sequence_no: int,
    payload: Any,
    agent_id: str | None = None,
    timestamp: str = "",
    random_seed_context: Mapping[str, Any] | None = None,
    causality_refs: Sequence[str] = (),
) -> EventEnvelope:
    """构造并校验一条强类型事件；``event_id`` 由内容寻址生成。

    校验项：payload 类型、必填信封字段、agent_id 适用性、状态相关事件的
    因果证据、``skipped`` 指标不得带数值。
    """
    try:
        et = EventType(event_type)
    except ValueError as exc:
        raise EventError(f"未注册的事件类型：{event_type}") from exc

    expected = EVENT_PAYLOAD_TYPES[et]
    if not isinstance(payload, expected):
        raise EventError(
            f"{et.value} 的 payload 必须是 {expected.__name__}，"
            f"实际 {type(payload).__name__}；禁止用通用字典绕过类型约束"
        )
    for key in ("simulation_id", "experiment_id"):
        if not locals()[key]:
            raise EventError(f"{key} 不能为空")
    if int(tick) < 0 or int(sequence_no) < 0:
        raise EventError("tick 与 sequence_no 必须为非负整数")
    if et in EVENT_AGENT_REQUIRED and not agent_id:
        raise EventError(f"{et.value} 必须提供 agent_id")

    payload_dict = asdict(payload)
    if et is EventType.METRIC_RECORDED:
        if payload.status == "skipped":
            if payload.value is not None:
                raise EventError("MetricRecorded(status=skipped) 不得携带数值")
            if not payload.reason:
                raise EventError("MetricRecorded(status=skipped) 必须给出 reason")
    if et is EventType.NETWORK_EDGE_ADDED and payload.network_type == "influence":
        if payload.relation_kind not in ("known_causal", "candidate", "inferred"):
            raise EventError("影响网络边必须显式声明 relation_kind（Q-24）")
        if not payload.evidence_refs:
            raise EventError("影响网络边必须携带 evidence_refs，禁止仅凭相关/时序建边（Q-24）")

    body = {
        "event_type": et.value,
        "simulation_id": simulation_id,
        "experiment_id": experiment_id,
        "tick": int(tick),
        "sequence_no": int(sequence_no),
        "agent_id": agent_id,
        "payload": payload_dict,
        "causality_refs": list(causality_refs),
    }
    event_id = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()[:24]
    return EventEnvelope(
        event_id=event_id,
        event_type=et.value,
        simulation_id=simulation_id,
        experiment_id=experiment_id,
        tick=int(tick),
        sequence_no=int(sequence_no),
        payload=payload_dict,
        agent_id=agent_id,
        timestamp=timestamp,
        random_seed_context=dict(random_seed_context or {}),
        causality_refs=tuple(causality_refs),
    )


def validate_order(prev: EventEnvelope, cur: EventEnvelope) -> list[str]:
    """校验相邻两条事件的时序；返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    if cur.sequence_no != prev.sequence_no + 1:
        errors.append(
            f"sequence_no 不连续：{prev.sequence_no} → {cur.sequence_no}"
        )
    if cur.tick < prev.tick:
        errors.append(f"tick 倒退：{prev.tick} → {cur.tick}")
    if cur.tick == prev.tick:
        p, c = EVENT_PHASE[EventType(prev.event_type)], EVENT_PHASE[EventType(cur.event_type)]
        if c < p:
            errors.append(
                f"同 tick 内事件顺序违规：{prev.event_type}(phase {p}) "
                f"→ {cur.event_type}(phase {c})"
            )
    for ref in cur.causality_refs:
        if not ref:
            errors.append("causality_refs 不得含空引用")
    return errors
