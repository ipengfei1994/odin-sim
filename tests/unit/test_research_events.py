"""S2 验收：强类型事件、时序、回放、三条分离硬约束。"""
from __future__ import annotations

from pathlib import Path

import pytest

from odin_sim.research.events import (
    EVENT_PAYLOAD_TYPES, ContentCreated, ContentExposed, EventEnvelope,
    EventError, EventType, ExposureSelected, InteractionCreated,
    MetricRecorded, NetworkEdgeAdded, make_event, validate_order,
)
from odin_sim.research.recorder import (
    EventRecorder, RecorderError, read_events, replay_digest,
)
from odin_sim.research.records import (
    AgentSnapshot, DecisionRecord, InfluenceRecord, InteractionEvent,
    NetworkSnapshot, SeparationGuard,
)


def ev(et, payload, *, seq, tick=0, agent_id="a1", **kw):
    return make_event(
        et, simulation_id="sim", experiment_id="exp", tick=tick,
        sequence_no=seq, payload=payload, agent_id=agent_id, **kw
    )


def test_all_12_event_types_have_typed_payloads():
    assert len(EVENT_PAYLOAD_TYPES) == 12
    assert set(EVENT_PAYLOAD_TYPES) == set(EventType)
    for et, cls in EVENT_PAYLOAD_TYPES.items():
        assert isinstance(cls, type)
        assert et.value


def test_wrong_payload_type_rejected():
    with pytest.raises(EventError, match="禁止用通用字典"):
        ev(EventType.CONTENT_CREATED, {"content_id": "c1"}, seq=0)


def test_agent_id_required_for_agent_events():
    with pytest.raises(EventError, match="agent_id"):
        make_event(EventType.CONTENT_CREATED, simulation_id="s", experiment_id="e",
                   tick=0, sequence_no=0, payload=ContentCreated("c1"), agent_id=None)


def test_event_id_is_content_addressed_and_stable():
    e1 = ev(EventType.CONTENT_CREATED, ContentCreated("c1", text_hash="h"), seq=0)
    e2 = ev(EventType.CONTENT_CREATED, ContentCreated("c1", text_hash="h"), seq=0)
    e3 = ev(EventType.CONTENT_CREATED, ContentCreated("c1", text_hash="h"), seq=1)
    assert e1.event_id == e2.event_id
    assert e1.event_id != e3.event_id
    assert len(e1.event_id) == 24


def test_skipped_metric_must_not_carry_value():
    with pytest.raises(EventError, match="不得携带数值"):
        ev(EventType.METRIC_RECORDED, MetricRecorded(
            "M1-BC", value=0.9, status="skipped", reason="TBD"), seq=0, agent_id=None)
    ok = ev(EventType.METRIC_RECORDED, MetricRecorded(
        "M1-BC", value=None, status="skipped", reason="TBD"), seq=0, agent_id=None)
    assert ok.payload["value"] is None


def test_q24_influence_edge_requires_evidence():
    with pytest.raises(EventError, match="evidence_refs"):
        ev(EventType.NETWORK_EDGE_ADDED, NetworkEdgeAdded(
            "influence", src="a", dst="b", relation_kind="known_causal"),
            seq=0, agent_id=None)
    ok = ev(EventType.NETWORK_EDGE_ADDED, NetworkEdgeAdded(
        "influence", src="a", dst="b", relation_kind="known_causal",
        evidence_refs=("exp-1", "tr-1")), seq=0, agent_id=None)
    assert ok.payload["relation_kind"] == "known_causal"


def test_event_phase_order_validation():
    a = ev(EventType.CONTENT_EXPOSED, ContentExposed("e1", "c1"), seq=0)
    b = ev(EventType.EXPOSURE_SELECTED, ExposureSelected("e1", "c1"), seq=1)
    c = ev(EventType.CONTENT_CREATED, ContentCreated("c2"), seq=2)
    assert validate_order(a, b) == []
    errors = validate_order(b, c)
    assert errors and "顺序违规" in errors[0]
    assert any("不连续" in e for e in validate_order(a, c))


def test_recorder_roundtrip_and_digest(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    events = [
        ev(EventType.CONTENT_CREATED, ContentCreated("c1"), seq=0),
        ev(EventType.CONTENT_EXPOSED, ContentExposed("e1", "c1"), seq=1, agent_id="a2"),
        ev(EventType.EXPOSURE_SELECTED, ExposureSelected("e1", "c1"), seq=2, agent_id="a2"),
    ]
    with EventRecorder(path) as rec:
        rec.append_all(events)
        digest = rec.digest
        rec.write_digest()
    loaded = read_events(path)
    assert [e.event_type for e in loaded] == [e.event_type for e in events]
    assert replay_digest(loaded) == digest
    assert (tmp_path / "events.sha256").read_text(encoding="utf-8").strip() == digest


def test_recorder_rejects_phase_regression(tmp_path: Path):
    with EventRecorder(tmp_path / "events.jsonl") as rec:
        rec.append(ev(EventType.CONTENT_EXPOSED, ContentExposed("e1", "c1"), seq=0))
        with pytest.raises(RecorderError, match="顺序违规"):
            rec.append(ev(EventType.CONTENT_CREATED, ContentCreated("c1"), seq=1))


def test_envelope_typed_payload_roundtrip():
    e = ev(EventType.INTERACTION_CREATED, InteractionCreated(
        "i1", "like", content_id="c1", decision_ref="d1"), seq=0)
    restored = EventEnvelope.from_dict(e.to_dict())
    assert isinstance(restored.typed_payload, InteractionCreated)
    assert restored.typed_payload.decision_ref == "d1"


# --- 三条分离（T-DATA/1–4） ------------------------------------------------
def test_s1_opinion_separation():
    snap = AgentSnapshot("s", "e", 0, "a1", opinion_true=(0.4,),
                         opinion_projected=(0.41,), projector_id="p", projector_version="1")
    assert SeparationGuard.check_opinion(snap) == []
    bad = AgentSnapshot("s", "e", 0, "a1", opinion_projected=(0.41,))
    assert any("projector_id" in e for e in SeparationGuard.check_opinion(bad))


def test_s2_intent_execution_separation():
    decisions = [
        DecisionRecord("d1", "a1", 0, "post", execution_status="executed"),
        DecisionRecord("d2", "a2", 0, "post", execution_status="rejected",
                       rejection_reason="engine_rejected"),
    ]
    interactions = [InteractionEvent("i1", "a1", 0, "post", decision_ref="d1")]
    assert SeparationGuard.check_intent_execution(decisions, interactions) == []
    bad = interactions + [InteractionEvent("i2", "a2", 0, "post", decision_ref="d2")]
    assert any("被拒绝的意图" in e for e in SeparationGuard.check_intent_execution(decisions, bad))
    no_ref = [InteractionEvent("i3", "a1", 0, "like")]
    assert any("无意图引用" in e for e in SeparationGuard.check_intent_execution(decisions, no_ref))


def test_s3_three_networks_separation():
    snaps = [NetworkSnapshot(f"{k}@0", k, 0, 10) for k in ("potential", "exposure", "influence")]
    assert SeparationGuard.check_networks(snaps) == []
    assert any("缺少 exposure" in e for e in SeparationGuard.check_networks([snaps[0]]))


def test_q24_influence_record_requires_evidence_and_method():
    assert SeparationGuard.check_influence(InfluenceRecord(
        "i1", "a", "b", 0, "known_causal", evidence_refs=("exp-1",))) == []
    errors = SeparationGuard.check_influence(InfluenceRecord(
        "i2", "a", "b", 0, "inferred"))
    assert len(errors) == 3
