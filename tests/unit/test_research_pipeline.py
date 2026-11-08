"""端到端验收：S1~S5 串联、产物、复现、不伪造事件、不伪造指标。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from odin_sim.research.config import ExperimentConfig
from odin_sim.research.metrics import DefinitionRegistry
from odin_sim.research.pipeline import (
    ResearchResult, run_research_experiment, verify_run_replay,
)


def base(**overrides):
    data = {
        "name": "pipeline-test", "model": "synthetic",
        "population_size": 6, "seeds": [1], "simulation_steps": 2,
        "sampling_interval_k": 1,
        "community_partition_method": "explicit_label",
        "opinion_dynamics_config": {"gov_learning_rate": 0.02},
        "recommendation_policy": {"selection_mode": "voluntary"},
    }
    data.update(overrides)
    return data


def test_end_to_end_artifacts_and_skip_all(tmp_path: Path):
    cfg = ExperimentConfig.from_dict(base())
    result: ResearchResult = run_research_experiment(cfg, out_root=tmp_path)
    assert result.ok is True
    assert result.total_runs == 1
    assert result.executed == 1
    assert result.failed == 0
    assert result.total_events > 0
    assert result.computed_metrics == 0
    assert result.skipped_metrics == 75
    assert result.separation_errors == {}

    rd = result.run_dirs[0]
    for name in (
        "events.jsonl", "events.sha256", "records.json", "observables.json",
        "metrics.json", "experiment_manifest.json",
        "network_potential.json", "network_exposure.json",
        "network_influence.json", "network_interaction.json",
    ):
        assert (rd / name).is_file(), f"缺少产物 {name}"
    assert (tmp_path / "research_manifest.json").is_file()
    assert (tmp_path / "experiment_config.json").is_file()

    metrics = json.loads((rd / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["n_total"] == 75 and metrics["n_computed"] == 0
    assert metrics["n_skipped"] == 75
    assert metrics["thresholds_locked"] is False
    assert all(m["value"] is None for m in metrics["skipped_metrics"])
    assert all("S0 未完成" in m["reason"] for m in metrics["skipped_metrics"])

    manifest = json.loads((rd / "experiment_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["s0_completed"] is False
    assert manifest["separation_errors"] == []
    assert manifest["n_events"] == result.total_events

    sweep = json.loads((tmp_path / "research_manifest.json").read_text(encoding="utf-8"))
    assert sweep["s0_completed"] is False
    assert sweep["open_questions_blocking"] == 27
    assert sweep["counts"]["skipped_metrics"] == 75


def test_event_replay_and_bitwise_reproducibility(tmp_path: Path):
    cfg = ExperimentConfig.from_dict(base())
    r1 = run_research_experiment(cfg, out_root=tmp_path / "a")
    r2 = run_research_experiment(cfg, out_root=tmp_path / "b")
    d1 = (r1.run_dirs[0] / "events.sha256").read_text(encoding="utf-8")
    d2 = (r2.run_dirs[0] / "events.sha256").read_text(encoding="utf-8")
    assert d1 == d2
    assert verify_run_replay(r1.run_dirs[0])["ok"] is True
    assert r1.total_events == r2.total_events


def test_three_separations_are_enforced_in_pipeline(tmp_path: Path):
    cfg = ExperimentConfig.from_dict(base())
    result = run_research_experiment(cfg, out_root=tmp_path)
    records = json.loads((result.run_dirs[0] / "records.json").read_text(encoding="utf-8"))
    snaps = records["agent_snapshots"]
    assert snaps
    assert all(s["opinion_true"] != s["opinion_projected"] for s in snaps)
    assert all(s["projector_id"] and s["projector_version"] for s in snaps)
    # 被拒绝的意图不得出现在执行事件中
    rejected = {d["decision_id"] for d in records["decisions"]
                if d["execution_status"] == "rejected"}
    executed = {i["decision_ref"] for i in records["interactions"]}
    assert rejected.isdisjoint(executed)
    kinds = {s["network_type"] for s in records["network_snapshots"]}
    assert {"potential", "exposure", "influence", "interaction"} <= kinds
    # 影响网络边必须携带证据
    influence = json.loads(
        (result.run_dirs[0] / "network_influence.json").read_text(encoding="utf-8")
    )
    for edge in influence["edges"]:
        assert edge["evidence_refs"]
        assert edge["relation_kind"] == "known_causal"


def test_trajectory_model_does_not_fabricate_events(tmp_path: Path):
    cfg = ExperimentConfig.from_dict(base(model="g3-baseline-v1"))
    result = run_research_experiment(cfg, out_root=tmp_path)
    assert result.ok is True
    assert result.event_level_unavailable == 1
    assert result.executed == 0
    assert result.total_events == 0
    assert not (result.run_dirs[0] / "events.jsonl").exists()
    manifest = json.loads(
        (result.run_dirs[0] / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["event_level_unavailable"] is True
    assert "事后无法重建" in manifest["reason"]


def test_registered_definitions_produce_values_only_when_locked(tmp_path: Path):
    cfg = ExperimentConfig.from_dict(base(
        definition_version={"metric_def_version": "example-v0"},
        opinion_dynamics_config={"ctrl_epsilon": 0.25, "gov_learning_rate": 0.02},
        extras={"opinion_norm": "l1"},
    ))
    registry = DefinitionRegistry(load_examples=True)
    result = run_research_experiment(cfg, out_root=tmp_path, registry=registry)
    assert result.ok is True
    assert result.computed_metrics == 2
    assert result.skipped_metrics == 73
    metrics = json.loads((result.run_dirs[0] / "metrics.json").read_text(encoding="utf-8"))
    computed = {m["metric_id"]: m for m in metrics["computed"]}
    assert set(computed) == {"M0-D", "P-ctrl-epsilon"}
    assert computed["M0-D"]["value"] >= 0
    assert computed["P-ctrl-epsilon"]["value"] == pytest.approx(0.25)
    assert all(m["input_refs"] for m in metrics["computed"])


def test_process_quantities_recorded_at_occurrence(tmp_path: Path):
    """曝光/选择/候选集合必须在事件中实时可查，而非事后反推。"""
    from odin_sim.research.recorder import read_events

    cfg = ExperimentConfig.from_dict(base())
    result = run_research_experiment(cfg, out_root=tmp_path)
    events = read_events(result.run_dirs[0] / "events.jsonl")
    exposed = [e for e in events if e.event_type == "ContentExposed"]
    selected = [e for e in events if e.event_type == "ExposureSelected"]
    assert exposed and selected
    assert all(e.payload["candidate_set_ids"] for e in exposed)
    assert all("visible_seconds" in e.payload for e in exposed)
    assert all(e.payload["selection_mode"] for e in selected)
    assert all(e.causality_refs for e in selected)
    # 3A 每 k 轮采样：k=1 时每 tick 有 4 张快照
    records = json.loads((result.run_dirs[0] / "records.json").read_text(encoding="utf-8"))
    assert len(records["network_snapshots"]) == 4 * cfg.simulation_steps
