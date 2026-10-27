"""批量运行器的单元测试（用假模型，不依赖 baseline）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from odin_sim.engine.runner import run_once
from odin_sim.experiments import SweepError, SweepSpec, run_sweep
from odin_sim.model.protocol import (
    ModelCapabilities,
    RunOutput,
    RunRequest,
)
from odin_sim.storage import layout
from odin_sim.storage.manifest import read_manifest, read_results_csv


class FakeModel:
    """只验证调度逻辑：数值形状合法、哈希可预测、可按需抛错。"""

    name = "fake-v1"
    capabilities = ModelCapabilities(
        stepwise=False, multi_dim=True, requires_control=False
    )

    def __init__(self, fail_on: int | None = None) -> None:
        self.fail_on = fail_on

    def run(self, request: RunRequest) -> RunOutput:
        if self.fail_on is not None and request.seed == self.fail_on:
            raise RuntimeError(f"fake failure seed={request.seed}")
        rng = np.random.default_rng(request.seed)
        traj = rng.normal(size=(5, 4, 2))
        series = {"extreme": np.linspace(0.0, 1.0, 5)}
        return RunOutput(
            traj=traj,
            is_adv=np.zeros(4, dtype=bool),
            is_gov=np.zeros(4, dtype=bool),
            series_ord=series,
            series_all=dict(series),
            cost={"n_adv": 1},
            meta={"traj_hash": f"t{request.seed}", "network_hash": f"n{request.seed}"},
            model_name=self.name,
            params={"tau_ext": 0.6},
        )


def _spec(seeds=(1, 2), on_error="abort", **kw) -> SweepSpec:
    data = {
        "name": "unit-sweep",
        "model": "fake-v1",
        "base": {"mode": "cross", "placement": "hub"},
        "axes": {"placement": ["hub", "random"]},
        "seeds": list(seeds),
        "on_error": on_error,
    }
    data.update(kw)
    return SweepSpec.from_dict(data)


def test_sweep_executes_and_writes_artifacts(tmp_path):
    result = run_sweep(_spec(), out_root=tmp_path, model=FakeModel())

    assert result.total == 4
    assert (result.executed, result.skipped, result.failed) == (4, 0, 0)
    assert result.ok

    csv_path = tmp_path / "results.csv"
    assert csv_path.is_file()
    rows = read_results_csv(tmp_path)
    assert len(rows) == 4
    assert all(r["status"] == "ok" for r in rows)
    assert {r["traj_hash"] for r in rows} == {"t1", "t2"}

    for row in rows:
        manifest = read_manifest(layout.run_dir(tmp_path, row["run_id"]))
        assert manifest is not None
        assert manifest["status"] == "ok"
        assert manifest["spec"]["seed"] == int(row["seed"])
        assert manifest["result"]["series_final_ord"]["extreme"] == 1.0


def test_sweep_is_idempotent(tmp_path):
    first = run_sweep(_spec(), out_root=tmp_path, model=FakeModel())
    second = run_sweep(_spec(), out_root=tmp_path, model=FakeModel())

    assert second.executed == 0
    assert second.skipped == 4
    assert [r["traj_hash"] for r in first.rows] == [r["traj_hash"] for r in second.rows]


def test_changing_spec_creates_new_runs(tmp_path):
    run_sweep(_spec(seeds=(1, 2)), out_root=tmp_path, model=FakeModel())
    changed = run_sweep(_spec(seeds=(3, 4)), out_root=tmp_path, model=FakeModel())

    assert changed.executed == 4
    assert changed.skipped == 0
    assert len(read_results_csv(tmp_path)) == 4  # 汇总表只含本次 sweep


def test_store_trajectory(tmp_path):
    result = run_sweep(
        _spec(store_trajectory=True), out_root=tmp_path, model=FakeModel()
    )
    assert result.executed == 4
    for row in result.rows:
        npz = layout.trajectory_path(layout.run_dir(tmp_path, row["run_id"]))
        assert npz.is_file()
        with np.load(npz) as data:
            assert data["traj"].shape == (5, 4, 2)


def test_abort_on_error_keeps_completed_runs(tmp_path):
    # 展开顺序为 (hub,1) (hub,2) (random,1) (random,2)；seed=2 失败即中止
    model = FakeModel(fail_on=2)
    with pytest.raises(SweepError, match="中止"):
        run_sweep(_spec(), out_root=tmp_path, model=model)

    # 失败的运行也留下 manifest，且状态明确
    manifests = [read_manifest(p) for p in (tmp_path / "runs").iterdir()]
    statuses = sorted(m["status"] for m in manifests if m)
    assert statuses == ["error", "ok"]

    rows = read_results_csv(tmp_path)
    assert len(rows) == 1  # 只保留失败前已完成的
    assert rows[0]["status"] == "ok"


def test_continue_on_error(tmp_path):
    result = run_sweep(
        _spec(on_error="continue"), out_root=tmp_path, model=FakeModel(fail_on=2)
    )
    assert result.failed == 2
    assert result.executed == 2
    assert result.aborted is False
    assert result.ok is False
    assert len(result.rows) == 2


def test_manifest_records_environment_and_sweep_summary(tmp_path):
    run_sweep(_spec(), out_root=tmp_path, model=FakeModel())
    summary = json.loads((tmp_path / "sweep_manifest.json").read_text(encoding="utf-8"))
    assert summary["counts"] == {"total": 4, "executed": 4, "skipped": 0, "failed": 0}
    assert summary["spec"]["model"] == "fake-v1"
    assert "git_commit" in summary["environment"]


def test_rows_cover_every_axis_combination(tmp_path):
    result = run_sweep(_spec(seeds=(1,)), out_root=tmp_path, model=FakeModel())
    assert {(r["placement"], r["seed"]) for r in result.rows} == {("hub", 1), ("random", 1)}


def test_engine_record_matches_direct_call(tmp_path):
    """实验层的哈希必须与直接调用引擎一致（防止调度层篡改输入）。"""
    model = FakeModel()
    direct = run_once(model, RunRequest(mode="cross", seed=1))
    result = run_sweep(_spec(seeds=(1,)), out_root=tmp_path, model=model)
    assert result.rows[0]["traj_hash"] == direct.output.traj_hash
