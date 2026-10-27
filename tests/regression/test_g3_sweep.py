"""实验层针对 G3 模型的回归测试。

验证三件事：
1. 走实验层产出的轨迹哈希，与直接调用引擎逐位一致；
2. 重跑同一 sweep 全部命中已完成结果（幂等 + 断点续跑）；
3. 需要对照的指标被**显式跳过并记录原因**，而不是被退化计算。
"""
from __future__ import annotations

import pytest

from odin_sim.engine.runner import run_once
from odin_sim.experiments import SweepSpec, expand_sweep, run_sweep
from odin_sim.model import DYNAMICS
from odin_sim.model.protocol import RunRequest
from odin_sim.storage import layout
from odin_sim.storage.manifest import read_manifest, read_results_csv

MODEL = "g3-baseline-v1"


def _spec(seeds=(1, 2)) -> SweepSpec:
    return SweepSpec.from_dict({
        "name": "g3-sweep-regression",
        "model": MODEL,
        "base": {
            "mode": "cross",
            "placement": "hub",
            "beta_gov": 0.20,
            "tau": 60,
            "scenario": "polarized",
        },
        "axes": {"beta_gov": [0.0, 0.2]},
        "seeds": list(seeds),
    })


@pytest.fixture(scope="module")
def model():
    if not DYNAMICS.has(MODEL):
        pytest.skip("G3 适配器未注册（外部 baseline 不可用）")
    return DYNAMICS.get(MODEL)


def test_sweep_matches_direct_engine_run(tmp_path, model):
    result = run_sweep(_spec(), out_root=tmp_path, metrics=["all"])

    assert result.total == 4
    assert (result.executed, result.failed) == (4, 0)

    for run, row in zip(expand_sweep(_spec()), result.rows):
        direct = run_once(model, run.to_request())
        assert row["traj_hash"] == direct.output.traj_hash
        assert row["network_hash"] == direct.output.network_hash


def test_metrics_are_computed_and_control_metrics_are_skipped(tmp_path, model):
    result = run_sweep(_spec(seeds=(1,)), out_root=tmp_path, metrics=["all"])

    row = result.rows[0]
    assert "g3.extreme_final_ord.extreme_final_ord" in row
    assert "g3.joint_final_ord.joint_final_ord" in row
    assert "g3.alert_lead_time.lead_time" in row or row.get(
        "g3.alert_lead_time.censored"
    ) is not None

    manifest = read_manifest(layout.run_dir(tmp_path, row["run_id"]))
    skipped = {m["name"] for m in manifest["skipped_metrics"]}
    assert "g3.backfire" in skipped
    assert "g3.backfire.bf_final" not in row


def test_rerun_is_skipped(tmp_path, model):
    first = run_sweep(_spec(seeds=(1,)), out_root=tmp_path, metrics=["all"])
    second = run_sweep(_spec(seeds=(1,)), out_root=tmp_path, metrics=["all"])

    assert second.executed == 0
    assert second.skipped == first.total
    assert [r["traj_hash"] for r in first.rows] == [r["traj_hash"] for r in second.rows]
    assert len(read_results_csv(tmp_path)) == first.total
