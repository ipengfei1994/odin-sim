"""S2｜数据记录对象（data-schema.md 的九类核心对象 + 辅助对象）。

三条分离硬约束（v2 / 规范 §3）
------------------------------
S-1 Agent **内部真实观点**与**文本投影器推断观点**分别记录；
S-2 Agent **行为意图**与**引擎实际执行的行为**分别记录；
S-3 **潜在关系 / 实际曝光关系 / 影响推断关系**分别记录。

本模块只定义数据形状与校验，不做任何推断、不补默认值。所有对象都可
无损 JSON 往返，供 Recorder 落盘与回放。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "AgentSnapshot", "ContentRecord", "ExposureEvent", "InteractionEvent",
    "DecisionRecord", "StateTransitionRecord", "NetworkSnapshot",
    "InfluenceRecord", "ExperimentMetadata",
    "ProjectionRecord", "HumanAnnotationRecord", "IsolationProbeRecord",
    "CommunityPartitionResult",
    "NETWORK_TYPES", "ACTION_TYPES", "RELATION_KINDS", "SeparationGuard",
    "to_jsonable",
]

NETWORK_TYPES = ("potential", "exposure", "influence", "interaction")
ACTION_TYPES = ("post", "noop", "comment", "like", "reshare")
RELATION_KINDS = ("known_causal", "candidate", "inferred")


def to_jsonable(obj: Any) -> dict[str, Any]:
    """dataclass → 可 JSON 化字典（键序不影响内容寻址，见 recorder）。"""
    return asdict(obj)


# ---------------------------------------------------------------------------
# 九类核心对象
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentSnapshot:
    simulation_id: str
    experiment_id: str
    tick: int
    agent_id: str
    opinion_true: tuple[float, ...] = ()
    """S-1：Agent 内部真实观点。禁止由投影值反写。"""
    opinion_projected: tuple[float, ...] = ()
    """S-1：文本投影器推断的观点；可与真值不同，且必须可追溯投影器版本。"""
    projector_id: str = ""
    projector_version: str = ""
    persona_vector: tuple[float, ...] = ()
    affect_state: tuple[float, ...] = ()
    belief_state: tuple[float, ...] = ()
    group_label: str = ""
    session_id: str = ""
    backend_id: str = ""


@dataclass(frozen=True)
class ContentRecord:
    content_id: str
    author_id: str
    tick: int
    text: str = ""
    text_hash: str = ""
    topic: str = ""
    sentiment: float | None = None
    source: str = "agent"


@dataclass(frozen=True)
class ExposureEvent:
    exposure_id: str
    agent_id: str
    content_id: str
    tick: int
    source_agent_id: str = ""
    recommendation_rank: int = -1
    candidate_set_ids: tuple[str, ...] = ()
    """可选项集合：选择性暴露（Q-26）的原始依据，事后无法重建。"""
    in_viewport: bool = False
    visible_seconds: float = 0.0
    selected: bool | None = None
    selection_mode: str = ""
    """voluntary / forced（Levy 2021 vs Bail 2018 的对照）。"""


@dataclass(frozen=True)
class InteractionEvent:
    interaction_id: str
    agent_id: str
    tick: int
    action_type: str
    """必须是 :data:`ACTION_TYPES` 之一。"""
    content_id: str = ""
    target_agent_id: str = ""
    decision_ref: str = ""
    """S-2：指向被执行的意图；无意图的执行判为非法。"""
    executed: bool = True


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    agent_id: str
    tick: int
    intent_action: str
    """S-2：Agent 产生的行为意图，与是否被执行无关。"""
    execution_status: str = "pending"
    """pending / executed / rejected。"""
    rejection_reason: str = ""
    intent_payload: dict[str, Any] = field(default_factory=dict)
    exposure_ref: str = ""
    llm_call_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StateTransitionRecord:
    transition_id: str
    agent_id: str
    tick: int
    before: tuple[float, ...] = ()
    after: tuple[float, ...] = ()
    cause_event_id: str = ""
    mechanism: str = ""
    """达成机制的证据（v2：报共识必须同时报达成机制）。"""
    causal_edge_kind: str = ""
    """known_causal / candidate / inferred；禁止留空后由消费方脑补。"""


@dataclass(frozen=True)
class NetworkSnapshot:
    snapshot_id: str
    network_type: str
    tick: int
    n_nodes: int
    edges_ref: str = ""
    directed: bool = True
    weighted: bool = False
    node_id_space: str = "agent_id"


@dataclass(frozen=True)
class InfluenceRecord:
    influence_id: str
    src_agent_id: str
    dst_agent_id: str
    tick: int
    relation_kind: str
    """S-3 / Q-24：必须显式给出证据类型，禁止仅凭相关或时序自动认定因果。"""
    evidence_refs: tuple[str, ...] = ()
    inferred_method: str = ""
    confidence: float | None = None


@dataclass(frozen=True)
class ExperimentMetadata:
    experiment_id: str
    simulation_id: str
    config_fingerprint: str
    config_version: str
    definition_version: dict[str, Any] = field(default_factory=dict)
    reproducibility_level: str = "bitwise"
    model_metadata: dict[str, Any] = field(default_factory=dict)
    recommendation_policy: dict[str, Any] = field(default_factory=dict)
    intervention_config: dict[str, Any] = field(default_factory=dict)
    initial_opinion_distribution: dict[str, Any] = field(default_factory=dict)
    opinion_dynamics_config: dict[str, Any] = field(default_factory=dict)
    population_size: int = 0
    created_at: str = ""


# ---------------------------------------------------------------------------
# 辅助记录对象（data-schema.md §2）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectionRecord:
    projection_id: str
    text_hash: str
    projector_id: str
    projector_version: str
    projected_value: tuple[float, ...] = ()
    confidence: float | None = None
    retest_batch_id: str = ""


@dataclass(frozen=True)
class HumanAnnotationRecord:
    annotation_id: str
    target_ref: str
    value: Any = None
    annotator_id: str = ""
    round: int = 0
    agreement_flag: bool | None = None
    source: str = "external"


@dataclass(frozen=True)
class IsolationProbeRecord:
    probe_id: str
    agent_id: str
    prompt: str = ""
    response: str = ""
    backend_id: str = ""
    prompt_version: str = ""


@dataclass(frozen=True)
class CommunityPartitionResult:
    partition_id: str
    method: str
    node_to_community: dict[str, int] = field(default_factory=dict)
    tick: int = -1
    version: str = ""


# ---------------------------------------------------------------------------
# 三条分离硬约束
# ---------------------------------------------------------------------------
class SeparationGuard:
    """字段级验收（T-DATA/1–3）。返回错误列表，空列表表示通过。

    校验是**只读**的：不修改任何记录，也不尝试"修复"违规数据。
    """

    @staticmethod
    def check_opinion(snapshot: AgentSnapshot) -> list[str]:
        errors: list[str] = []
        if (
            snapshot.opinion_true
            and snapshot.opinion_projected
            and snapshot.opinion_true is snapshot.opinion_projected
        ):
            errors.append("S-1 违规：真实观点与投影观点是同一对象，投影值会反写真值")
        if snapshot.opinion_projected and not snapshot.projector_id:
            errors.append("S-1 违规：存在投影观点但未记录 projector_id")
        if snapshot.opinion_projected and not snapshot.projector_version:
            errors.append("S-1 违规：存在投影观点但未记录 projector_version")
        return errors

    @staticmethod
    def check_intent_execution(
        decisions: Sequence[DecisionRecord],
        interactions: Sequence[InteractionEvent],
    ) -> list[str]:
        errors: list[str] = []
        by_id = {d.decision_id: d for d in decisions}
        for it in interactions:
            if it.executed and not it.decision_ref:
                errors.append(f"S-2 违规：已执行行为 {it.interaction_id} 无意图引用")
            elif it.executed and it.decision_ref not in by_id:
                errors.append(f"S-2 违规：{it.interaction_id} 引用了不存在的意图")
            elif it.executed and by_id[it.decision_ref].execution_status != "executed":
                errors.append(
                    f"S-2 违规：{it.interaction_id} 对应意图状态为 "
                    f"{by_id[it.decision_ref].execution_status}"
                )
        executed_refs = {it.decision_ref for it in interactions if it.executed}
        for d in decisions:
            if d.execution_status == "rejected" and d.decision_id in executed_refs:
                errors.append(f"S-2 违规：被拒绝的意图 {d.decision_id} 产生了执行事件")
        return errors

    @staticmethod
    def check_networks(snapshots: Sequence[NetworkSnapshot]) -> list[str]:
        errors: list[str] = []
        by_type: dict[str, list[NetworkSnapshot]] = {}
        for snap in snapshots:
            if snap.network_type not in NETWORK_TYPES:
                errors.append(f"S-3 违规：未知网络类型 {snap.network_type}")
            by_type.setdefault(snap.network_type, []).append(snap)
        for required in ("potential", "exposure", "influence"):
            if required not in by_type:
                errors.append(f"S-3 违规：缺少 {required} 网络快照")
        for snap in snapshots:
            if not snap.node_id_space:
                errors.append("S-3 违规：NetworkSnapshot 未声明 node_id_space")
        return errors

    @staticmethod
    def check_influence(record: InfluenceRecord) -> list[str]:
        """Q-24：影响网络边必须有证据，且不能只凭相关/时序。"""
        errors: list[str] = []
        if record.relation_kind not in RELATION_KINDS:
            errors.append(f"Q-24 违规：未知关系类型 {record.relation_kind}")
        if not record.evidence_refs:
            errors.append("Q-24 违规：影响边缺少 evidence_refs，不得仅凭相关或时序建边")
        if record.relation_kind == "inferred" and not record.inferred_method:
            errors.append("Q-24 违规：inferred 边必须声明推断方法")
        if record.relation_kind == "inferred" and record.confidence is None:
            errors.append("Q-24 违规：inferred 边必须给出置信度")
        return errors

    @classmethod
    def check_all(
        cls,
        snapshots: Iterable[AgentSnapshot] = (),
        decisions: Sequence[DecisionRecord] = (),
        interactions: Sequence[InteractionEvent] = (),
        networks: Sequence[NetworkSnapshot] = (),
        influences: Sequence[InfluenceRecord] = (),
    ) -> list[str]:
        errors: list[str] = []
        for snap in snapshots:
            errors.extend(cls.check_opinion(snap))
        errors.extend(cls.check_intent_execution(decisions, interactions))
        errors.extend(cls.check_networks(networks))
        for rec in influences:
            errors.extend(cls.check_influence(rec))
        return errors


def dumps_records(records: Sequence[Any]) -> str:
    """JSONL 序列化（每行一个对象），供 Recorder 复用。"""
    return "".join(
        json.dumps(asdict(r), ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for r in records
    )
