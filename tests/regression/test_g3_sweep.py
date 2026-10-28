"""实验层针对 G3 模型的回归测试。

验证三件事：
1. 走实验层产出的轨迹哈希，与直接调用引擎逐位一致；
2. 重跑同一 sweep 全部命中已完成结果（幂等 + 断点续跑）；
3. 需要对照的指标被**显式跳过并记录原因**，而不是被退化计算。
"""
from __future__ import annotations

import pytest

from odin_sim.engine.runner import run_once
from odin_sim.experiments import (
    ROLE_CONTROL,
    ROLE_MAIN,
    SweepSpec,
    expand_sweep,
    run_sweep,
)
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


def test_control_spec_makes_backfire_computed(tmp_path, model):
    """可配置对照口径：配置 control 后，requires_control 指标 g3.backfire
    真正配对计算，而非被跳过。

    对照口径取 baseline 自身的"无治理"语义：mode=none（同种子）。平台不内置
    任何口径，这里只是把研究的对照判断写成配置，随实验走。
    """
    spec = SweepSpec.from_dict({
        "name": "g3-control-backfire",
        "model": MODEL,
        "base": {
            "mode": "cross",
            "placement": "hub",
            "beta_gov": 0.20,
            "tau": 60,
            "scenario": "polarized",
        },
        "axes": {},
        "seeds": [1],
        "control": {
            "name": "no-governance",
            "overrides": {"mode": "none"},
        },
    })

    result = run_sweep(spec, out_root=tmp_path, metrics=["all"])

    # 1 条对照（mode=none）+ 1 条主运行（mode=cross）
    assert result.total == 2
    assert (result.executed, result.failed) == (2, 0)

    rows = {r["role"]: r for r in result.rows}
    main_row = rows[ROLE_MAIN]

    # 主运行：backfire 必须被计算（存在且不为跳过）
    assert "g3.backfire.bf_final" in main_row
    assert isinstance(main_row["g3.backfire.bf_final"], bool)

    main_manifest = read_manifest(layout.run_dir(tmp_path, main_row["run_id"]))
    skipped_main = {m["name"] for m in main_manifest["skipped_metrics"]}
    assert "g3.backfire" not in skipped_main

    # 对照运行：不再计算需要对照的指标（避免无限递归）
    ctrl_row = rows[ROLE_CONTROL]
    assert "g3.backfire.bf_final" not in ctrl_row
    ctrl_manifest = read_manifest(layout.run_dir(tmp_path, ctrl_row["run_id"]))
    skipped_ctrl = {m["name"]: m["reason"] for m in ctrl_manifest["skipped_metrics"]}
    assert "g3.backfire" in skipped_ctrl
    assert "递归" in skipped_ctrl["g3.backfire"]

    # 对照轨迹必须已落盘：否则重跑时配对指标会拿不到对照
    ctrl_id = main_row["control_run_id"]
    assert ctrl_id == ctrl_row["run_id"]
    assert layout.trajectory_path(layout.run_dir(tmp_path, ctrl_id)).is_file()


def test_control_requires_different_spec_from_main(tmp_path, model):
    """对照变换后若与主运行某条完全相同，应直接报错而非静默复用。"""
    spec = _spec(seeds=(1,)).to_dict()
    # 主运行轴里有 mode=cross，再加一条 control 把 cross 变 none——但若轴上
    # 已含 none，则派生出的对照与某条主运行冲突。这里用与主运行相同的 mode
    # 施加覆盖，制造冲突。
    spec["axes"] = {"mode": ["cross", "none"]}
    spec["control"] = {"overrides": {"mode": "none"}}
    with pytest.raises(Exception, match="完全相同"):
        run_sweep(SweepSpec.from_dict(spec), out_root=tmp_path, metrics=["all"])
