"""内置参考模型（reference-v1）测试：不依赖 odin_g3 内核。

这是"内核缺失也能端到端测试平台"的核心证明——用纯 Python 参考模型实际跑
sweep，验证产物 schema 与数值合法性，而非依赖外部 G3 内核。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from odin_sim.experiments import SweepSpec, run_sweep
from odin_sim.model import DYNAMICS, METRICS
from odin_sim.model.adapters.reference import (
    REFERENCE_NAME,
    RefAlertLead,
    RefBackfire,
    RefExtremeFinal,
    RefJointFinal,
    ReferenceDynamics,
    _extreme_flag,
)
from odin_sim.model.protocol import DynamicsModel, Metric, RunRequest


def test_reference_registered_without_kernel():
    assert REFERENCE_NAME in DYNAMICS.names()
    for name in (
        "ref.extreme_final_ord",
        "ref.joint_final_ord",
        "ref.backfire",
        "ref.alert_lead_time",
    ):
        assert name in METRICS.names()


def test_reference_satisfies_protocol():
    assert isinstance(ReferenceDynamics(), DynamicsModel)
    for cls in (RefExtremeFinal, RefJointFinal, RefBackfire, RefAlertLead):
        assert isinstance(cls(), Metric)


def test_reference_is_deterministic():
    req = RunRequest(
        mode="cross", seed=7, placement="hub",
        beta_gov=0.3, tau=40, scenario="polarized",
    )
    out1 = ReferenceDynamics().run(req)
    out2 = ReferenceDynamics().run(req)
    assert np.array_equal(out1.traj, out2.traj)
    assert out1.traj.shape == (151, 200, 2)


def test_reference_run_schema():
    req = RunRequest(
        mode="cross", seed=3, scenario="polarized", beta_gov=0.2, tau=60
    )
    out = ReferenceDynamics().run(req)
    assert out.traj.shape == (151, 200, 2)
    assert out.is_adv.shape == (200,)
    assert out.is_gov.shape == (200,)
    for key in ("extreme", "joint", "mean_opinion"):
        assert out.series_ord[key].shape == (151,)
        assert out.series_all[key].shape == (151,)
        assert out.series_ord[key].min() >= 0.0
        assert out.series_ord[key].max() <= 1.0
    assert out.params["tau_ext"] == 0.8


def test_reference_governance_reduces_extreme():
    """beta_gov 越大，普通节点极端占比末值越低（演示治理效应）。"""

    def extreme_final(beta: float) -> float:
        req = RunRequest(
            mode="cross", seed=5, scenario="polarized", beta_gov=beta, tau=0
        )
        out = ReferenceDynamics().run(req)
        mask = ~(out.is_adv | out.is_gov)
        return float(_extreme_flag(out.traj[-1], 0.8)[mask].mean())

    no_gov = extreme_final(0.0)
    with_gov = extreme_final(0.4)
    assert no_gov > with_gov
    assert no_gov > 0.9


def test_reference_response_is_graded_not_all_or_nothing():
    """极端占比随 beta_gov **分级下降**，而非"全体齐步跨阈值"的全有或全无。

    这是 UI 曲线可读性的前提：若响应是阶跃，扫描 beta_gov 只会得到 1 → 0 的
    一堵墙，且种子间零方差（无 CI 带）。个体易感性异质性正是为此而设。
    """
    grid = [0.0, 0.1, 0.2, 0.4, 0.7, 1.0]

    def extreme_final(beta: float, seed: int) -> float:
        out = ReferenceDynamics().run(
            RunRequest(
                mode="cross", seed=seed, scenario="polarized", beta_gov=beta, tau=60
            )
        )
        mask = ~(out.is_adv | out.is_gov)
        return float(_extreme_flag(out.traj[-1], 0.8)[mask].mean())

    means = [float(np.mean([extreme_final(b, s) for s in (1, 2, 3)])) for b in grid]

    # 单调下降
    assert all(a > b for a, b in zip(means, means[1:])), means
    # 端点接近饱和，中间必须"在中间"——若退化成阶跃，中段只会是 0 或 1
    assert means[0] > 0.9
    assert means[-1] < 0.3
    assert 0.1 < means[2] < 0.9  # beta=0.2 处应有实质梯度
    # 种子间存在有限方差（否则聚合后没有置信带）
    spread = max(extreme_final(0.2, s) for s in (1, 2, 3)) - min(
        extreme_final(0.2, s) for s in (1, 2, 3)
    )
    assert spread > 0.0


def _ref_spec() -> SweepSpec:
    return SweepSpec.from_dict(
        {
            "name": "ref-test",
            "model": REFERENCE_NAME,
            "base": {
                "mode": "cross",
                "placement": "hub",
                "beta_gov": 0.2,
                "tau": 60,
                "scenario": "polarized",
            },
            "axes": {"beta_gov": [0.0, 0.4]},
            "seeds": [1, 2],
            "store_trajectory": False,
            "on_error": "abort",
        }
    )


def test_reference_sweep_end_to_end(tmp_path: Path):
    out = tmp_path / "ref_run"
    result = run_sweep(_ref_spec(), out_root=str(out), metrics=["all"], echo=False)
    assert result.total == 4
    assert result.failed == 0
    assert result.executed == 4

    assert (out / "results.csv").is_file()
    assert (out / "sweep_manifest.json").is_file()
    df = pd.read_csv(out / "results.csv")
    assert len(df) == 4
    for col in (
        "ref.extreme_final_ord.extreme_final_ord",
        "ref.joint_final_ord.joint_final_ord",
        "ref.alert_lead_time.alert_step",
    ):
        assert col in df.columns
    assert df["ref.extreme_final_ord.extreme_final_ord"].between(0, 1).all()

    run_dirs = [p for p in (out / "runs").iterdir() if p.is_dir()]
    assert len(run_dirs) == 4
    for rd in run_dirs:
        assert (rd / "timeseries.csv").is_file()


def test_reference_sweep_with_control_computes_backfire(tmp_path: Path):
    spec = SweepSpec.from_dict(
        {
            "name": "ref-ctrl",
            "model": REFERENCE_NAME,
            "base": {
                "mode": "cross",
                "placement": "hub",
                "tau": 60,
                "scenario": "polarized",
            },
            "axes": {"beta_gov": [0.4]},
            "seeds": [1],
            "control": {
                "overrides": {"beta_gov": 0.0},
                "seed": "same",
                "name": "no-governance",
            },
        }
    )
    out = tmp_path / "ref_ctrl"
    result = run_sweep(spec, out_root=str(out), metrics=["all"], echo=False)
    df = pd.read_csv(out / "results.csv")
    assert "ref.backfire.bf_final" in df.columns
    assert result.failed == 0


def test_default_model_is_registered():
    from odin_sim.ui.pages import configure as cfg_page

    assert DYNAMICS.has(cfg_page.default_model_name())
