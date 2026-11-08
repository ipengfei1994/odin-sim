"""S3/S4 验收：三网络持久化、图运算、指标门控与定义一致性。"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from odin_sim.research.config import DEFINITION_UNSET, ExperimentConfig
from odin_sim.research.metrics import (
    METRIC_IDS, TBD_METRIC_IDS, DefinitionRegistry, MetricInput, SkipReason,
    compute_all, observables,
)
from odin_sim.research.metrics.compute import ObservableError, pairwise_distance
from odin_sim.research.networks import (
    NetworkError, NetworkGraph, NetworkStore, avg_shortest_path_length,
    cross_edge_ratio, density, edge_count, jaccard_overlap,
)
from odin_sim.research.records import (
    AgentSnapshot, CommunityPartitionResult,
)


def base(**overrides):
    data = {
        "name": "t", "model": "synthetic", "population_size": 6,
        "seeds": [1], "simulation_steps": 2,
        "community_partition_method": "explicit_label",
    }
    data.update(overrides)
    return data


def test_four_network_types_and_persistence(tmp_path: Path):
    graphs = {
        kind: NetworkGraph(kind, directed=(kind != "potential"), node_ids=["a", "b", "c"])
        for kind in ("potential", "exposure", "influence", "interaction")
    }
    graphs["potential"].add_edge("a", "b", tick=0)
    graphs["exposure"].add_edge("a", "b", tick=1)
    graphs["interaction"].add_edge("b", "c", tick=1)
    graphs["influence"].add_influence(
        "a", "b", tick=1, relation_kind="known_causal", evidence_refs=("exp-1",)
    )
    written = NetworkStore.save(graphs, tmp_path)
    assert set(written) == set(graphs)
    loaded = NetworkStore.load_all(tmp_path)
    assert set(loaded) == set(graphs)
    assert edge_count(loaded["potential"]) == 1
    assert loaded["influence"].active_edges()[0].evidence_refs == ("exp-1",)
    assert loaded["influence"].active_edges()[0].relation_kind == "known_causal"


def test_q24_influence_edge_without_evidence_rejected():
    g = NetworkGraph("influence")
    with pytest.raises(NetworkError, match="证据引用"):
        g.add_influence("a", "b", relation_kind="known_causal", evidence_refs=())
    with pytest.raises(NetworkError, match="推断方法"):
        g.add_influence("a", "b", relation_kind="inferred", evidence_refs=("e",))
    with pytest.raises(NetworkError, match="置信度"):
        g.add_influence("a", "b", relation_kind="inferred", evidence_refs=("e",),
                        inferred_method="granger")
    g.add_influence("a", "b", relation_kind="inferred", evidence_refs=("e",),
                    inferred_method="granger", confidence=0.8)
    assert edge_count(g) == 1


def test_edge_lifecycle():
    g = NetworkGraph("potential", directed=False, node_ids=["a", "b"])
    g.add_edge("a", "b", tick=0)
    assert len(g.active_edges(tick=0)) == 1
    g.remove_edge("a", "b", tick=3)
    assert len(g.active_edges(tick=2)) == 1
    assert len(g.active_edges(tick=3)) == 0
    assert len(g.active_edges()) == 0
    assert len(g.to_dict()["all_edges"]) == 1  # 历史不丢失


def test_known_community_structure_cross_edge_ratio():
    g = NetworkGraph("potential", directed=False, node_ids=["a", "b", "c", "d"])
    g.add_edge("a", "b")
    g.add_edge("c", "d")
    g.add_edge("b", "c")  # 唯一跨社区边
    partition = {"a": 0, "b": 0, "c": 1, "d": 1}
    assert cross_edge_ratio(g, partition) == pytest.approx(1 / 3)
    assert edge_count(g) == 3
    assert density(g) == pytest.approx(0.5)


def test_known_chain_average_path_and_jaccard():
    g = NetworkGraph("potential", directed=False, node_ids=["a", "b", "c"])
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    # a: 1+2, b: 1+1, c: 1+2 → 8 / 6
    assert avg_shortest_path_length(g) == pytest.approx(8 / 6)
    h = NetworkGraph("exposure", directed=True, node_ids=["a", "b", "c"])
    h.add_edge("a", "b")
    assert jaccard_overlap(g, h) == pytest.approx(0.5)


def test_metric_ids_match_specification_document():
    doc = (Path(__file__).resolve().parents[2]
           / "docs" / "research" / "experiment-specification-v1.md")
    import re
    ids = set(re.findall(r'^- metric_id: "([^"]+)"',
                         doc.read_text(encoding="utf-8"), re.M))
    assert len(METRIC_IDS) == 75
    assert set(METRIC_IDS) == ids
    assert len(TBD_METRIC_IDS) == 43


def test_all_metrics_skipped_when_s0_unset():
    cfg = ExperimentConfig.from_dict(base())
    assert cfg.definition_version.metric_def_version == DEFINITION_UNSET
    outcomes = compute_all(MetricInput(config=cfg))
    assert len(outcomes) == 75
    assert all(o.skipped for o in outcomes)
    assert all(o.value is None for o in outcomes)
    assert all(o.reason == SkipReason.S0_UNSET for o in outcomes)


def test_tbd_metrics_never_produce_values_even_with_version_set():
    cfg = ExperimentConfig.from_dict(base(definition_version={
        "metric_def_version": "metric_def_v1"
    }))
    outcomes = compute_all(MetricInput(config=cfg))
    assert all(o.skipped for o in outcomes)
    tbd = [o for o in outcomes if o.metric_id in TBD_METRIC_IDS]
    assert len(tbd) == 43
    assert all(o.reason == SkipReason.TBD for o in tbd)


def test_registered_example_definition_computes_known_value():
    cfg = ExperimentConfig.from_dict(base(
        definition_version={"metric_def_version": "example-v0"},
        opinion_dynamics_config={"ctrl_epsilon": 0.25},
        extras={"opinion_norm": "l1"},
    ))
    snapshots = [
        AgentSnapshot("s", "e", 0, "a", opinion_true=(0.0,), opinion_projected=(0.01,),
                      projector_id="p", projector_version="1"),
        AgentSnapshot("s", "e", 0, "b", opinion_true=(0.5,), opinion_projected=(0.51,),
                      projector_id="p", projector_version="1"),
        AgentSnapshot("s", "e", 0, "c", opinion_true=(1.0,), opinion_projected=(1.01,),
                      projector_id="p", projector_version="1"),
    ]
    inp = MetricInput(config=cfg, records={"agent_snapshots": snapshots})
    reg = DefinitionRegistry(load_examples=True)
    m0d = reg.compute("M0-D", inp)
    eps = reg.compute("P-ctrl-epsilon", inp)
    assert m0d.status == "computed"
    assert m0d.value == pytest.approx(2.0 / 3.0)
    assert m0d.input_refs == ("agent_snapshots",)
    assert eps.value == pytest.approx(0.25)


def test_norm_must_be_explicit():
    assert pairwise_distance([0.0, 1.0], norm="l1") == pytest.approx(1.0)
    with pytest.raises(ObservableError, match="Q-42"):
        pairwise_distance([0.0, 1.0], norm="")


def test_observables_do_not_use_v2_metric_names():
    cfg = ExperimentConfig.from_dict(base())
    g = NetworkGraph("potential", directed=False, node_ids=["a", "b"])
    g.add_edge("a", "b")
    part = [CommunityPartitionResult("p1", "explicit_label", {"a": 0, "b": 1})]
    obs = observables({"community_partition_result": part}, {"potential": g}, config=cfg)
    assert "M1-BC" not in obs and "3A-Q" not in obs
    assert obs["potential_edge_count"] == 1
    assert obs["metric_def_version"] == DEFINITION_UNSET
    assert obs["thresholds_locked"] is False
    assert math.isnan(obs["potential_avg_shortest_path_lcc"]) or obs["potential_avg_shortest_path_lcc"] >= 0
