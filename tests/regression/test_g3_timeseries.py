"""时间序列落盘的 G3 回归测试。

核心比对的 prod 依据：**``timeseries.csv`` 的末点必须逐位等于 manifest 里
的 ``series_final_*``**。

这两个值来源不同（一个来自模型产出的完整序列，一个来自同序列的尾部取值），
任何一处出现精度截断、行列错位或序列错位，这个等号就会断。因此它是时间序列
层最有力的正确性锚点——不需要肉眼判断图对不对。
"""
from __future__ import annotations

import pytest

from odin_sim.analysis import aggregate_timeseries, load_run_series
from odin_sim.experiments import SweepSpec, run_sweep
from odin_sim.model import DYNAMICS
from odin_sim.storage import layout
from odin_sim.storage.manifest import read_manifest, read_timeseries_csv

MODEL = "g3-baseline-v1"


def _spec():
    return SweepSpec.from_dict({
        "name": "g3-timeseries-regression",
        "model": MODEL,
        "base": {
            "mode": "cross",
            "placement": "hub",
            "beta_gov": 0.20,
            "tau": 60,
            "scenario": "polarized",
        },
        "axes": {"beta_gov": [0.0, 0.2]},
        "seeds": [1, 2],
        "store_trajectory": True,
    })


@pytest.fixture(scope="module")
def model():
    if not DYNAMICS.has(MODEL):
        pytest.skip("G3 适配器未注册（外部 baseline 不可用）")
    return DYNAMICS.get(MODEL)


@pytest.fixture(scope="module")
def sweep_out(tmp_path_factory, model):
    root = tmp_path_factory.mktemp("g3_ts") / "sweep"
    result = run_sweep(_spec(), out_root=root, metrics=[])
    assert result.failed == 0
    return root


def test_timeseries_csv_exists_for_every_run(sweep_out, model):
    manifests = sorted((sweep_out / "runs").iterdir())
    assert manifests
    for rd in manifests:
        manifest = read_manifest(rd)
        assert manifest is not None
        assert "timeseries.csv" in manifest["artifacts"]
        assert layout.timeseries_path(rd).is_file()


def test_series_tail_equals_manifest_final_values(sweep_out, model):
    """锚点：序列末点 == manifest 的 series_final_*（精确相等，非近似）。"""
    checked = 0
    for rd in sorted((sweep_out / "runs").iterdir()):
        manifest = read_manifest(rd)
        series = read_timeseries_csv(rd)
        assert series is not None, f"{rd} 缺少时间序列"

        # 步数应与模型声明的 steps 一致（steps+1 含初始状态）
        n_steps = int(manifest["result"]["params"]["steps"]) + 1
        assert len(series["step"]) == n_steps

        for group, prefix in (("series_final_ord", "ord_"), ("series_final_all", "all_")):
            for key, expected in manifest["result"][group].items():
                got = float(series[prefix + key][-1])
                assert got == expected, (
                    f"{rd.name} 的 {prefix}{key} 末点 {got!r} 与 manifest 的 "
                    f"{expected!r} 不一致——时间序列与终值已脱节"
                )
                checked += 1
    assert checked >= 4  # 每个 run 至少 ord/all 两组的 extreme、joint


def test_params_are_persisted(sweep_out, model):
    """复算任何依赖阈值的量都需要实际参数，不能退回默认值。"""
    for rd in sorted((sweep_out / "runs").iterdir()):
        params = read_manifest(rd)["result"]["params"]
        assert params, "params 未落盘，时间序列将无法脱离默认值复算"
        assert "tau_ext" in params
        assert "tau_joint" in params
        # 联合阈值是对角线口径 sqrt(2)*tau_ext
        assert params["tau_joint"] == pytest.approx(params["tau_ext"] * 2 ** 0.5)


def test_aggregate_over_seeds(sweep_out, model):
    records = load_run_series(sweep_out)
    assert len(records) == 4  # 2 个 beta_gov × 2 个种子

    rows = aggregate_timeseries(records, group_by=["beta_gov"])
    assert len(rows) == 2
    for row in rows:
        assert row["ord_extreme.n"] == 2
        # 极端化占比是比例量，CI 不得越出 [0,1]
        assert float(row["ord_extreme.ci_low"].min()) >= 0.0
        assert float(row["ord_extreme.ci_high"].max()) <= 1.0
        assert row["ord_extreme.proportion"] is True


def test_control_runs_excluded_by_default(sweep_out, model):
    records = load_run_series(sweep_out)
    # 本 sweep 未配置对照，全部是主运行；显式取对照应为空
    assert load_run_series(sweep_out, role="control") == []
    assert len(load_run_series(sweep_out, role="all")) == len(records)


def test_rerun_does_not_redo_timeseries_work(sweep_out, model):
    """幂等：重跑应全部命中已有结果，且时间序列仍在（不被判定为需要重跑）。"""
    second = run_sweep(_spec(), out_root=sweep_out, metrics=[])
    assert second.executed == 0
    assert second.skipped == second.total
    for rd in sorted((sweep_out / "runs").iterdir()):
        assert layout.timeseries_path(rd).is_file()
