"""对照配对机制的单元测试（用假模型，不依赖 baseline）。

验证的是**机制**，不是某个具体口径：
1. 对照口径由配置给出，平台不内置；
2. 对照先跑、主运行后跑，配对指标能拿到对照轨迹；
3. 四种"拿不到对照"的情形都显式跳过并记录原因，不编造数字；
4. 幂等重跑后配对指标仍然可用（对照轨迹已落盘）。
"""
from __future__ import annotations

import numpy as np
import pytest

from odin_sim.experiments import (
    CONTROL_LINKED,
    CONTROL_NONE,
    CONTROL_SELF,
    ROLE_CONTROL,
    ROLE_MAIN,
    ControlSpec,
    SpecError,
    SweepSpec,
    expand_sweep,
    run_sweep,
)
from odin_sim.model.protocol import (
    MetricContext,
    ModelCapabilities,
    RunOutput,
    RunRequest,
)
from odin_sim.model.registry import METRICS
from odin_sim.storage import layout
from odin_sim.storage.manifest import read_manifest


class FakeModel:
    """只验证调度逻辑：轨迹可预测、参数齐全。"""

    name = "fake-v1"
    capabilities = ModelCapabilities(
        stepwise=False, multi_dim=True, requires_control=False
    )

    def run(self, request: RunRequest) -> RunOutput:
        rng = np.random.default_rng(request.seed)
        base = rng.normal(size=(5, 4, 2))
        # 让不同 mode 产生可观测差异：对照（mode=none）与主运行轨迹不同，
        # 从而验证"配对指标拿到的确实是对照轨迹，而非主运行自身"。
        traj = base + (0.0 if request.mode == "none" else 0.7)
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


class PairedMetric:
    """需要配对对照的假指标：输出对照与自身末值之差。"""

    name = "fake.paired"
    requires_control = True

    def compute(self, ctx: MetricContext) -> dict[str, object]:
        if ctx.control_traj is None:
            raise ValueError("fake.paired 需要 control_traj")
        return {"delta_final": float(ctx.traj[-1].mean() - ctx.control_traj[-1].mean())}


class PlainMetric:
    name = "fake.plain"
    requires_control = False

    def compute(self, ctx: MetricContext) -> dict[str, object]:
        return {"mean_final": float(ctx.traj[-1].mean())}


@pytest.fixture(autouse=True)
def _register_fake_metrics():
    METRICS.register(PairedMetric.name, PairedMetric(), overwrite=True)
    METRICS.register(PlainMetric.name, PlainMetric(), overwrite=True)
    yield
    for name in (PairedMetric.name, PlainMetric.name):
        if METRICS.has(name):
            METRICS.unregister(name)


def _spec(**kw) -> SweepSpec:
    data = {
        "name": "control-sweep",
        "model": "fake-v1",
        "base": {"mode": "cross", "placement": "hub"},
        "axes": {"mode": ["cross", "direct"]},
        "seeds": [1, 2],
    }
    data.update(kw)
    return SweepSpec.from_dict(data)


def _control(**kw) -> dict:
    base = {"name": "no-governance", "overrides": {"mode": "none"}}
    base.update(kw)
    return base


def test_control_spec_rejects_bad_fields():
    with pytest.raises(SpecError, match="不能为空"):
        ControlSpec.from_dict({})
    with pytest.raises(SpecError, match="键非法"):
        ControlSpec.from_dict({"overrides": {"seed": 7}})
    with pytest.raises(SpecError, match="键非法"):
        ControlSpec.from_dict({"overrides": {"nonsense": 1}})
    with pytest.raises(SpecError, match="只允许"):
        ControlSpec.from_dict({"overrides": {"mode": "none"}, "seed": "other"})
    with pytest.raises(SpecError, match="apply_to 键非法"):
        ControlSpec.from_dict({"overrides": {"mode": "none"}, "apply_to": {"bad": [1]}})


def test_derive_applies_overrides_and_keeps_seed():
    spec = _spec()
    main = expand_sweep(spec)[0]
    ctrl = ControlSpec.from_dict(_control()).derive(main)
    assert ctrl.mode == "none"
    assert ctrl.seed == main.seed
    assert ctrl.role == ROLE_CONTROL
    # 未覆盖的字段沿用主运行
    assert ctrl.placement == main.placement
    assert ctrl.scenario == main.scenario


def test_derive_supports_param_overrides_and_explicit_seed():
    main = expand_sweep(_spec())[0]
    ctrl = ControlSpec.from_dict({
        "overrides": {"param_overrides.w_gov": 0.0},
        "seed": 999,
    }).derive(main)
    assert ctrl.param_overrides["w_gov"] == 0.0
    assert ctrl.seed == 999


def test_control_runs_precede_mains_and_are_deduplicated():
    runs = expand_sweep(_spec(control=_control()))
    roles = [r.role for r in runs]
    assert roles == [ROLE_CONTROL, ROLE_CONTROL, ROLE_MAIN, ROLE_MAIN,
                     ROLE_MAIN, ROLE_MAIN]
    # 2 个主运行组合 × 2 种子 = 4 条主运行，共享 2 条对照（每种子一条）
    controls = [r for r in runs if r.role == ROLE_CONTROL]
    assert len({r.run_id for r in controls}) == 2
    assert {r.seed for r in controls} == {1, 2}
    assert all(r.control_status == CONTROL_LINKED for r in runs if r.role == ROLE_MAIN)


def test_control_conflicting_with_main_run_is_an_error():
    """对照规格与某条主运行相同时必须报错，而不是静默复用或覆盖。"""
    with pytest.raises(SpecError, match="完全相同"):
        expand_sweep(_spec(
            axes={"mode": ["cross", "none"]},
            control=_control(),
        ))


def test_paired_metric_is_computed_with_control(tmp_path):
    result = run_sweep(
        _spec(seeds=[1], axes={"mode": ["cross"]}, control=_control()),
        out_root=tmp_path,
        metrics=["fake.plain", "fake.paired"],
        model=FakeModel(),
    )
    assert result.total == 2  # 1 条对照 + 1 条主运行
    rows = {r["role"]: r for r in result.rows}
    assert "fake.paired.delta_final" in rows[ROLE_MAIN]
    assert float(rows[ROLE_MAIN]["fake.paired.delta_final"]) != 0.0
    assert rows[ROLE_CONTROL]["control_run_id"] == ""


def test_control_run_does_not_compute_paired_metrics(tmp_path):
    result = run_sweep(
        _spec(seeds=[1], axes={"mode": ["cross"]}, control=_control()),
        out_root=tmp_path,
        metrics=["fake.paired"],
        model=FakeModel(),
    )
    ctrl_row = next(r for r in result.rows if r["role"] == ROLE_CONTROL)
    assert "fake.paired.delta_final" not in ctrl_row

    manifest = read_manifest(layout.run_dir(tmp_path, ctrl_row["run_id"]))
    reasons = {m["name"]: m["reason"] for m in manifest["skipped_metrics"]}
    assert "递归" in reasons["fake.paired"]


def test_paired_metric_skipped_when_no_control_configured(tmp_path):
    # 用显式指标名而非 "all"：避免 baseline 已注册 g3.* 指标时，
    # ["all"] 把依赖 G3 参数的指标纳入，与 FakeModel 的参数不匹配而误报错。
    # 本测试只验证"未配置 control 时 requires_control 指标被跳过"。
    result = run_sweep(
        _spec(seeds=[1], axes={"mode": ["cross"]}),
        out_root=tmp_path,
        metrics=["fake.plain", "fake.paired"],
        model=FakeModel(),
    )
    assert result.total == 1
    assert "fake.paired.delta_final" not in result.rows[0]
    manifest = read_manifest(layout.run_dir(tmp_path, result.rows[0]["run_id"]))
    reasons = {m["name"]: m["reason"] for m in manifest["skipped_metrics"]}
    assert "未配置 control" in reasons["fake.paired"]
    assert result.rows[0]["role"] == ROLE_MAIN


def test_apply_to_limits_which_runs_get_a_control(tmp_path):
    spec = _spec(seeds=[1], control=_control(apply_to={"mode": ["cross"]}))
    runs = expand_sweep(spec)
    by_mode = {r.mode: r for r in runs if r.role == ROLE_MAIN}
    assert by_mode["cross"].control_status == CONTROL_LINKED
    assert by_mode["direct"].control_status == CONTROL_NONE

    result = run_sweep(
        spec, out_root=tmp_path, metrics=["fake.paired"], model=FakeModel()
    )
    direct_row = next(r for r in result.rows
                      if r["role"] == ROLE_MAIN and r["mode"] == "direct")
    assert "fake.paired.delta_final" not in direct_row
    manifest = read_manifest(layout.run_dir(tmp_path, direct_row["run_id"]))
    reasons = {m["name"]: m["reason"] for m in manifest["skipped_metrics"]}
    assert "适用范围" in reasons["fake.paired"]


def test_self_control_is_reported_not_filled_with_zero(tmp_path):
    """口径为"对照就是自己"时，差值恒为零 = 无定义，平台不填 0。"""
    spec = _spec(axes={"mode": ["none"]}, seeds=[1],
                 control=_control(overrides={"mode": "none"}))
    runs = expand_sweep(spec)
    assert len(runs) == 1
    assert runs[0].control_status == CONTROL_SELF

    result = run_sweep(spec, out_root=tmp_path, metrics=["fake.paired"],
                       model=FakeModel())
    row = result.rows[0]
    assert "fake.paired.delta_final" not in row
    manifest = read_manifest(layout.run_dir(tmp_path, row["run_id"]))
    reasons = {m["name"]: m["reason"] for m in manifest["skipped_metrics"]}
    assert "自指" in reasons["fake.paired"]


def test_control_trajectory_is_persisted_and_reused_on_rerun(tmp_path):
    spec = _spec(seeds=[1], axes={"mode": ["cross"]}, control=_control())
    first = run_sweep(spec, out_root=tmp_path, metrics=["fake.paired"],
                      model=FakeModel())
    ctrl_id = next(r["run_id"] for r in first.rows if r["role"] == ROLE_CONTROL)
    assert layout.trajectory_path(layout.run_dir(tmp_path, ctrl_id)).is_file()

    second = run_sweep(spec, out_root=tmp_path, metrics=["fake.paired"],
                       model=FakeModel())
    assert second.executed == 0
    assert second.skipped == 2
    main_row = next(r for r in second.rows if r["role"] == ROLE_MAIN)
    # 重跑全部命中已完成，配对指标必须依然在表中（列不能悄悄消失）
    assert "fake.paired.delta_final" in main_row
    assert float(main_row["fake.paired.delta_final"]) == pytest.approx(
        float(next(r for r in first.rows
                   if r["role"] == ROLE_MAIN)["fake.paired.delta_final"])
    )


def test_control_status_and_name_are_recorded(tmp_path):
    spec = _spec(seeds=[1], axes={"mode": ["cross"]}, control=_control())
    run_sweep(spec, out_root=tmp_path, metrics=["fake.paired"], model=FakeModel())
    summary = (tmp_path / "sweep_manifest.json").read_text(encoding="utf-8")
    assert "no-governance" in summary  # control.name 随结果一起落盘
    assert '"mode": "none"' in summary


def test_control_seed_can_be_explicit(tmp_path):
    """对照也可以明确换种子族（口径问题，平台只负责照做并记录）。"""
    spec = _spec(seeds=[1], axes={"mode": ["cross"]},
                 control=_control(seed=4242))
    result = run_sweep(spec, out_root=tmp_path, metrics=["fake.paired"],
                       model=FakeModel())
    ctrl_row = next(r for r in result.rows if r["role"] == ROLE_CONTROL)
    assert int(ctrl_row["seed"]) == 4242
