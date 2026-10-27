"""引擎测试（用假模型，不依赖 baseline）。

引擎的职责只有三件事：分发请求、计时、组装产物。语义正确性由模型自己
和回归测试负责，这里只验证引擎不做多余的事、也不吞异常。
"""
from __future__ import annotations

import numpy as np
import pytest

from odin_sim.engine import run_many, run_once
from odin_sim.model.protocol import (
    DynamicsModel,
    MetricContext,
    ModelCapabilities,
    RunOutput,
    RunRequest,
)


class FakeDynamics:
    name = "fake"
    capabilities = ModelCapabilities(stepwise=True, multi_dim=False)

    def run(self, request: RunRequest) -> RunOutput:
        traj = np.zeros((3, 4, 1))
        return RunOutput(
            traj=traj,
            is_adv=np.zeros(4, dtype=bool),
            is_gov=np.zeros(4, dtype=bool),
            series_ord={"extreme": np.zeros(3)},
            series_all={"extreme": np.zeros(3)},
            cost={"n_gov": 0},
            meta={"traj_hash": "hash-" + request.config_id, "network_hash": "net"},
        )


class BoomDynamics:
    name = "boom"
    capabilities = ModelCapabilities()

    def run(self, request: RunRequest) -> RunOutput:
        raise ValueError("kernel exploded")


def test_fake_model_satisfies_protocol():
    assert isinstance(FakeDynamics(), DynamicsModel)


def test_run_once_fills_bookkeeping():
    rec = run_once(FakeDynamics(), RunRequest(mode="none", seed=1))
    assert rec.model_name == "fake"
    assert rec.traj_hash == "hash-none_ba_b000_t00"
    assert rec.output.wall_seconds >= 0.0
    assert rec.capabilities["stepwise"] is True
    assert rec.capabilities["multi_dim"] is False


def test_run_once_does_not_swallow_model_errors():
    with pytest.raises(ValueError, match="kernel exploded"):
        run_once(BoomDynamics(), RunRequest(mode="none", seed=1))


def test_run_many_preserves_request_order():
    reqs = [
        RunRequest(mode="none", seed=1),
        RunRequest(mode="direct", seed=2, placement="hub", beta_gov=0.2, tau=60),
    ]
    recs = run_many(FakeDynamics(), reqs)
    assert [r.request.seed for r in recs] == [1, 2]
    assert recs[1].request.config_id == "direct_hub_b200_t60"


def test_run_record_row_is_flat():
    rec = run_once(FakeDynamics(), RunRequest(mode="none", seed=1))
    row = rec.to_row()
    assert row["config_id"] == "none_ba_b000_t00"
    assert row["network_hash"] == "net"
    assert isinstance(row["wall_seconds"], float)


def test_metric_context_defaults_control_to_none():
    ctx = MetricContext(
        traj=np.zeros((2, 2, 2)),
        is_adv=np.zeros(2, dtype=bool),
        is_gov=np.zeros(2, dtype=bool),
        params={},
    )
    assert ctx.control_traj is None
