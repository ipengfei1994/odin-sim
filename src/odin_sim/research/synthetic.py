"""研究数据通道的**验证用合成模型**（不是研究结论，不替代 G3 / ODIN-Agents）。

用途
----
S2/S3/S4 需要一条能真实产生「内容 → 分发 → 曝光 → 选择 → 交互 → 意见变化」
事件链的载体，否则事件/网络/指标层只能测数据结构、无法端到端验证。本模块
提供一个纯 numpy、确定性、可逐步驱动的最小合成体：

* 意见更新规则是**占位机制**（向被选中曝光的立场做小步移动），不含任何
  v2 科学定义，不用于得出任何研究结论；
* 影响网络边只在模型**内部显式已知因果**（这次曝光导致了这次更新）时建
  ``known_causal`` 边，绝不靠观点相关或时间先后推断；
* 真实观点与投影观点、意图与执行、三张网络均分离记录，供 T-DATA/1–4
  做字段级验收。

生产环境应由 ODIN-Agents 提供真实 Agent 与投影器；届时只需实现
``StepwiseModel`` 协议即可替换本模型，数据通道不变。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Sequence

import numpy as np

from .events import (
    AgentDecisionMade, AgentStateChanged, ContentCreated, ContentExposed,
    ContentPublished, EventType, ExposureSelected, InteractionCreated,
    InterventionApplied, MetricRecorded, NetworkEdgeAdded, OpinionChanged,
)
from .networks import NetworkGraph
from .records import (
    ACTION_TYPES, AgentSnapshot, CommunityPartitionResult, ContentRecord,
    DecisionRecord, ExposureEvent, InfluenceRecord, InteractionEvent,
    NetworkSnapshot, StateTransitionRecord,
)
from .stepwise import EventDraft

__all__ = ["SyntheticPopulation"]

PROJECTOR_ID = "synthetic-projector-v0"
PROJECTOR_VERSION = "0.1.0"


class SyntheticPopulation:
    """确定性、可逐步驱动的最小意见—曝光仿真体。"""

    stepwise_capable = True

    def __init__(self, *, population_size: int | None = None, seed: int | None = None) -> None:
        self.override_n = population_size
        self.override_seed = seed
        self.config: Any = None
        self.n = 0
        self.seed = 0
        self.steps = 0
        self.tick = 0
        self.opinion: np.ndarray | None = None
        self.agent_ids: list[str] = []
        self.partition: dict[str, int] = {}
        self.records: dict[str, list[Any]] = defaultdict(list)
        self.graphs: dict[str, NetworkGraph] = {}
        self._content_opinion: dict[str, float] = {}
        self._pending_edges: list[EventDraft] = []

    # ------------------------------------------------------------------
    def initialize(self, context: Any) -> None:
        cfg = context["config"]
        self.config = cfg
        self.n = int(self.override_n or cfg.population_size)
        self.seed = int(self.override_seed if self.override_seed is not None else cfg.random_seed)
        self.steps = int(context["steps"])
        self.simulation_id = str(context["simulation_id"])
        self.experiment_id = str(context["experiment_id"])

        rng = np.random.default_rng([self.seed, self.n])
        self.agent_ids = [f"a{i:05d}" for i in range(self.n)]
        # 两个初始群：仅用于生成可观测结构，不宣称任何极化结论。
        groups = np.arange(self.n) % 2
        self.opinion = np.where(groups == 0, -0.45, 0.45) + rng.normal(0, 0.05, self.n)
        self.partition = {aid: int(g) for aid, g in zip(self.agent_ids, groups)}

        self.graphs = {
            "potential": NetworkGraph("potential", directed=False, node_ids=self.agent_ids),
            "exposure": NetworkGraph("exposure", directed=True, node_ids=self.agent_ids),
            "influence": NetworkGraph("influence", directed=True, node_ids=self.agent_ids),
            "interaction": NetworkGraph("interaction", directed=True, node_ids=self.agent_ids),
        }
        self.records.clear()
        self._content_opinion.clear()
        self._pending_edges.clear()

        # 潜在网络：环 + 少量确定性长边。边在 tick 0 一次性建立。
        edges: list[tuple[str, str]] = []
        for i in range(self.n):
            edges.append((self.agent_ids[i], self.agent_ids[(i + 1) % self.n]))
        extra = max(1, self.n // 10)
        for k in range(extra):
            a, b = self.agent_ids[k], self.agent_ids[(k * 7 + 3) % self.n]
            if a != b:
                edges.append((a, b))
        for a, b in edges:
            self.graphs["potential"].add_edge(a, b, tick=0)
            self._pending_edges.append(self._edge_draft("potential", a, b, tick=0))

        self.records["community_partition_result"].append(
            CommunityPartitionResult(
                partition_id=f"partition-{cfg.community_partition_method}-{self.seed}-{self.n}",
                method=cfg.community_partition_method,
                node_to_community=dict(self.partition),
                tick=0,
                version=cfg.definition_version.metric_def_version,
            )
        )

    # ------------------------------------------------------------------
    def step(self, tick: int) -> list[EventDraft]:
        """产生一个 tick 的事件草案，严格按 EVENT_PHASE 顺序排列。"""
        self.tick = tick
        rng = np.random.default_rng([self.seed, self.n, tick, 17])
        drafts: list[EventDraft] = []
        seed_ctx = {
            "seed": self.seed, "tick": tick, "population_size": self.n,
            "rng": "numpy.PCG64", "stream": "synthetic-step",
        }

        # --- phase 0：干预（若配置） -----------------------------------
        omega = int(self.config.intervention_config.get("gov_omega", 0))
        if omega > 0 and tick % omega == 0:
            intervention = InterventionApplied(
                intervention_id=f"iv-{tick}",
                gov_omega=omega,
                gov_tau=str(self.config.intervention_config.get("gov_tau", "random")),
                gov_kappa=str(self.config.intervention_config.get("gov_kappa", "none")),
            )
            drafts.append(EventDraft(EventType.INTERVENTION_APPLIED, intervention,
                                     label=f"iv-{tick}", random_seed_context=seed_ctx))

        # --- phase 1：意图（S-2：与执行分离） ---------------------------
        actions = rng.choice(np.array(ACTION_TYPES, dtype=object), size=self.n)
        decisions: list[DecisionRecord] = []
        for aid, action in zip(self.agent_ids, actions):
            did = f"dec-{tick}-{aid}"
            # 约 1/4 意图被引擎拒绝，用于验证「执行 ≠ 意图」。
            rejected = bool(rng.random() < 0.25)
            status = "rejected" if rejected else "executed"
            payload = AgentDecisionMade(
                decision_id=did, intent_action=str(action), execution_status=status,
                reason="engine_rejected" if rejected else "",
            )
            drafts.append(EventDraft(EventType.AGENT_DECISION_MADE, payload,
                                     agent_id=aid, label=did, random_seed_context=seed_ctx))
            decisions.append(DecisionRecord(
                decision_id=did, agent_id=aid, tick=tick, intent_action=str(action),
                execution_status=status,
                rejection_reason="engine_rejected" if rejected else "",
                llm_call_metadata={"backend": "synthetic", "temperature": None},
            ))
        self.records["decisions"].extend(decisions)
        by_agent = {d.decision_id: d for d in decisions}

        # --- phase 2/3：内容生成与发布（按 phase 分组，避免同 tick 回退）--
        published: list[tuple[str, str, float]] = []
        created_drafts: list[EventDraft] = []
        published_drafts: list[EventDraft] = []
        for aid, action in zip(self.agent_ids, actions):
            did = f"dec-{tick}-{aid}"
            dec = by_agent[did]
            if action != "post" or dec.execution_status != "executed":
                continue
            cid = f"c-{tick}-{aid}"
            idx = self.agent_ids.index(aid)
            stance = float(self.opinion[idx])  # type: ignore[index]
            text = f"synthetic content from {aid} at tick {tick}"
            self.records["contents"].append(ContentRecord(
                content_id=cid, author_id=aid, tick=tick, text=text,
                text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                topic="synthetic", sentiment=None, source="agent",
            ))
            self._content_opinion[cid] = stance
            created_drafts.append(EventDraft(EventType.CONTENT_CREATED,
                                     ContentCreated(cid, text_hash=hashlib.sha256(
                                         text.encode("utf-8")).hexdigest()[:16]),
                                     agent_id=aid, label=f"created-{cid}",
                                     random_seed_context=seed_ctx))
            published_drafts.append(EventDraft(EventType.CONTENT_PUBLISHED,
                                     ContentPublished(
                                         cid, distribution="potential_neighbors",
                                         candidate_count=max(1, self.n // 10),
                                         rec_alpha=self.config.recommendation_policy.get("rec_alpha"),
                                     ),
                                     label=f"published-{cid}",
                                     causal_labels=(f"created-{cid}",),
                                     random_seed_context=seed_ctx))
            published.append((cid, aid, stance))
        drafts.extend(created_drafts)
        drafts.extend(published_drafts)

        # --- phase 4/5：曝光与选择（过程量，实时记录） ------------------
        selected: list[tuple[str, str, str, float, str]] = []
        exposed_drafts: list[EventDraft] = []
        selected_drafts: list[EventDraft] = []
        for cid, author, stance in published:
            candidates = self._candidates(author, rng)
            for viewer in candidates:
                eid = f"exp-{cid}-{viewer}"
                visible = float(rng.uniform(0.2, 2.5))
                in_viewport = bool(visible >= 1.0)
                mode = str(self.config.recommendation_policy.get(
                    "selection_mode", "voluntary"
                ))
                # forced = 强制暴露（Bail 2018 式）；voluntary = 可自行选择（Levy 2021 式）
                chosen = True if mode == "forced" else bool(
                    in_viewport and rng.random() < 0.7
                )
                self.records["exposures"].append(ExposureEvent(
                    exposure_id=eid, agent_id=viewer, content_id=cid, tick=tick,
                    source_agent_id=author, recommendation_rank=len(candidates),
                    candidate_set_ids=tuple(candidates), in_viewport=in_viewport,
                    visible_seconds=visible, selected=chosen,
                    selection_mode=mode,
                ))
                exposed_drafts.append(EventDraft(EventType.CONTENT_EXPOSED,
                                         ContentExposed(
                                             eid, cid, source_agent_id=author,
                                             visible_seconds=visible,
                                             in_viewport=in_viewport,
                                             candidate_set_ids=tuple(candidates),
                                         ),
                                         agent_id=viewer, label=eid,
                                         causal_labels=(f"published-{cid}",),
                                         random_seed_context=seed_ctx))
                selected_drafts.append(EventDraft(EventType.EXPOSURE_SELECTED,
                                         ExposureSelected(eid, cid, selected=chosen,
                                                          selection_mode=mode),
                                         agent_id=viewer, label=f"sel-{eid}",
                                         causal_labels=(eid,), random_seed_context=seed_ctx))
                if chosen:
                    selected.append((eid, viewer, cid, stance, author))
                self.graphs["exposure"].add_edge(author, viewer, tick=tick)
                self._pending_edges.append(
                    self._edge_draft("exposure", author, viewer, tick=tick)
                )
        drafts.extend(exposed_drafts)
        drafts.extend(selected_drafts)

        # --- phase 6：实际执行的行为 -----------------------------------
        for eid, viewer, cid, stance, author in selected:
            did = f"dec-{tick}-{viewer}"
            dec = by_agent[did]
            if dec.execution_status != "executed":
                continue
            iid = f"int-{eid}"
            self.records["interactions"].append(InteractionEvent(
                interaction_id=iid, agent_id=viewer, tick=tick,
                action_type=dec.intent_action, content_id=cid,
                target_agent_id=author, decision_ref=did, executed=True,
            ))
            drafts.append(EventDraft(EventType.INTERACTION_CREATED,
                                     InteractionCreated(
                                         iid, dec.intent_action, content_id=cid,
                                         target_agent_id=author, decision_ref=did,
                                     ),
                                     agent_id=viewer, label=iid,
                                     causal_labels=(f"sel-{eid}", did),
                                     random_seed_context=seed_ctx))
            self.graphs["interaction"].add_edge(viewer, author, tick=tick)
            self._pending_edges.append(
                self._edge_draft("interaction", viewer, author, tick=tick)
            )

        # --- phase 7：意见变化（已知因果机制，显式证据） ----------------
        step_size = float(self.config.opinion_dynamics_config.get("gov_learning_rate", 0.02))
        for eid, viewer, cid, stance, author in selected:
            idx = self.agent_ids.index(viewer)
            before = float(self.opinion[idx])  # type: ignore[index]
            after = float(before + step_size * (stance - before))
            if abs(after - before) < 1e-12:
                continue
            self.opinion[idx] = after  # type: ignore[index]
            tid = f"tr-{eid}"
            self.records["transitions"].append(StateTransitionRecord(
                transition_id=tid, agent_id=viewer, tick=tick,
                before=(before,), after=(after,), cause_event_id=eid,
                mechanism="explicit_exposure_update",
                causal_edge_kind="known_causal",
            ))
            drafts.append(EventDraft(EventType.OPINION_CHANGED,
                                     OpinionChanged(
                                         tid, before=(before,), after=(after,),
                                         cause_exposure_id=eid,
                                         mechanism="explicit_exposure_update",
                                         causal_edge_kind="known_causal",
                                     ),
                                     agent_id=viewer, label=tid,
                                     causal_labels=(eid,), random_seed_context=seed_ctx))
            # 影响网络：模型内部**显式知道**是这次曝光导致了这次更新。
            self.graphs["influence"].add_influence(
                author, viewer, tick=tick, relation_kind="known_causal",
                evidence_refs=(eid, tid),
            )
            self.records["influences"].append(InfluenceRecord(
                influence_id=f"inf-{eid}", src_agent_id=author, dst_agent_id=viewer,
                tick=tick, relation_kind="known_causal", evidence_refs=(eid, tid),
            ))
            self._pending_edges.append(EventDraft(
                EventType.NETWORK_EDGE_ADDED,
                NetworkEdgeAdded("influence", src=author, dst=viewer,
                                 relation_kind="known_causal",
                                 evidence_refs=(eid, tid)),
                label=f"edge-inf-{eid}", causal_labels=(eid, tid),
                random_seed_context=seed_ctx,
            ))

        # --- phase 8：网络边事件（与边表一致） --------------------------
        drafts.extend(self._pending_edges)
        self._pending_edges.clear()

        # --- 每轮全量 AgentSnapshot（3C/3D） ---------------------------
        for i, aid in enumerate(self.agent_ids):
            true = (float(self.opinion[i]),)  # type: ignore[index]
            # 确定性"投影器"：带固定微小偏置，与真值分离但可追溯。
            projected = (round(float(true[0]) + 0.01, 6),)
            self.records["agent_snapshots"].append(AgentSnapshot(
                simulation_id=self.simulation_id, experiment_id=self.experiment_id,
                tick=tick, agent_id=aid, opinion_true=true,
                opinion_projected=projected, projector_id=PROJECTOR_ID,
                projector_version=PROJECTOR_VERSION,
                group_label=str(self.partition[aid]), session_id=f"s-{tick}",
                backend_id="synthetic",
            ))

        # --- 3A：每 k 轮网络快照 ---------------------------------------
        k = int(self.config.sampling_interval_k)
        if tick % k == 0:
            for kind, graph in self.graphs.items():
                self.records["network_snapshots"].append(
                    graph.snapshot(tick, snapshot_id=f"{kind}@{tick}")
                )
        return drafts

    # ------------------------------------------------------------------
    def _candidates(self, author: str, rng: np.random.Generator) -> list[str]:
        n_candidates = max(1, self.n // 10)
        # 优先潜在邻居，不足时按确定性规则补足；候选集合本身必须记录（Q-26）。
        neighbors = [
            e.dst if e.src == author else e.src
            for e in self.graphs["potential"].active_edges()
            if e.src == author or e.dst == author
        ]
        pool = list(dict.fromkeys(neighbors))
        if len(pool) < n_candidates:
            extras = [a for a in self.agent_ids if a not in pool and a != author]
            pick = rng.choice(np.array(extras, dtype=object),
                              size=min(len(extras), n_candidates - len(pool)),
                              replace=False) if extras else []
            pool.extend(str(x) for x in np.atleast_1d(pick))
        return sorted(pool[:n_candidates])

    @staticmethod
    def _edge_draft(kind: str, src: str, dst: str, *, tick: int) -> EventDraft:
        return EventDraft(
            EventType.NETWORK_EDGE_ADDED,
            NetworkEdgeAdded(kind, src=src, dst=dst),
            label=f"edge-{kind}-{src}-{dst}-{tick}",
            random_seed_context={"tick": tick},
        )

    def collect(self) -> tuple[dict[str, list[Any]], dict[str, NetworkGraph]]:
        return dict(self.records), self.graphs
