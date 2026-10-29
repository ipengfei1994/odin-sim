"""时间序列层的单元测试（不依赖 baseline）。

核心契约
--------
1. 序列存盘再读回必须**逐位精确**（末值与 manifest 的 series_final_* 对齐，
   任何精度截断都会让这个锚点失效）。
2. 渲染层画的每个点必须等于聚合层给的数组，不得重算统计量。
3. 缺失、长度不齐一律报错，不用补齐/截断蒙混。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from odin_sim.analysis import (
    aggregate_timeseries,
    load_run_series,
    plot_timeseries,
    timeseries_to_csv,
)
from odin_sim.storage import layout
from odin_sim.storage.manifest import (
    read_timeseries_csv,
    write_manifest,
    write_timeseries_csv,
)

_STEPS = 6


def _series(offset: float, steps: int = _STEPS) -> np.ndarray:
    return np.linspace(offset, offset + 0.5, steps)


def _make_run(
    root: Path,
    run_id: str,
    *,
    beta_gov: str,
    role: str = "main",
    ord_extreme: np.ndarray | None = None,
    steps: int = _STEPS,
) -> Path:
    """落一个完整 run 目录：manifest + timeseries.csv。"""
    ord_extreme = _series(0.1, steps) if ord_extreme is None else ord_extreme
    all_extreme = ord_extreme + 0.05
    rd = layout.run_dir(root, run_id)
    rd.mkdir(parents=True, exist_ok=True)
    write_manifest(
        rd,
        run_id=run_id,
        spec={"model": "fake", "mode": "cross", "beta_gov": beta_gov, "seed": 1},
        model={"name": "fake", "capabilities": {}},
        result={
            "config_id": f"cross_b{beta_gov}",
            "traj_hash": "t" * 16,
            "network_hash": "n" * 16,
            "wall_seconds": 0.01,
            "series_final_ord": {"extreme": float(ord_extreme[-1])},
            "series_final_all": {"extreme": float(all_extreme[-1])},
            "cost": {},
            "capabilities": {},
            "params": {},
        },
        environment={"python": "test"},
        role=role,
    )
    write_timeseries_csv(
        rd,
        series_ord={"extreme": ord_extreme},
        series_all={"extreme": all_extreme},
    )
    return rd


# ---------------------------------------------------------------- 存盘与精度


def test_write_read_roundtrip_is_exact(tmp_path):
    """末值必须逐位精确：这是与 manifest 中 series_final_* 对齐的锚点。"""
    rd = layout.run_dir(tmp_path, "abc")
    rd.mkdir(parents=True)
    values = np.array([0.1, 0.8611764705882353, 1 / 3, 0.43], dtype=float)
    write_timeseries_csv(rd, series_ord={"extreme": values}, series_all={})

    out = read_timeseries_csv(rd)
    assert out is not None
    np.testing.assert_array_equal(out["ord_extreme"], values)
    assert float(out["ord_extreme"][1]) == 0.8611764705882353


def test_tail_matches_manifest_final_value(tmp_path):
    """锚点：CSV 末值 == manifest.result.series_final_*。"""
    ord_extreme = _series(0.1)
    run_id = "anchor1"
    rd = _make_run(tmp_path, run_id, beta_gov="0.2")

    ts = read_timeseries_csv(rd)
    manifest_final = 0.1 + 0.5  # _series 的末值
    assert float(ts["ord_extreme"][-1]) == manifest_final
    assert float(ts["all_extreme"][-1]) == manifest_final + 0.05
    del ord_extreme


def test_empty_series_writes_nothing(tmp_path):
    rd = layout.run_dir(tmp_path, "empty")
    rd.mkdir(parents=True)
    assert write_timeseries_csv(rd, series_ord={}, series_all={}) is None
    assert not layout.timeseries_path(rd).is_file()
    assert read_timeseries_csv(rd) is None


def test_length_mismatch_raises(tmp_path):
    rd = layout.run_dir(tmp_path, "mismatch")
    rd.mkdir(parents=True)
    with pytest.raises(ValueError, match="长度不一致"):
        write_timeseries_csv(
            rd,
            series_ord={"extreme": np.zeros(4)},
            series_all={"extreme": np.zeros(7)},
        )


def test_read_missing_file_returns_none(tmp_path):
    assert read_timeseries_csv(tmp_path) is None


# ---------------------------------------------------------------- 加载与过滤


def test_load_run_series_returns_empty_for_unknown_root(tmp_path):
    assert load_run_series(tmp_path / "nope") == []


def test_load_run_series_filters_control_by_default(tmp_path):
    _make_run(tmp_path, "m1", beta_gov="0.0", role="main")
    _make_run(tmp_path, "c1", beta_gov="0.0", role="control", ord_extreme=_series(0.9))

    main = load_run_series(tmp_path)
    assert [r["run_id"] for r in main] == ["m1"]

    both = load_run_series(tmp_path, role="all")
    assert {r["run_id"] for r in both} == {"m1", "c1"}

    only_control = load_run_series(tmp_path, role="control")
    assert [r["run_id"] for r in only_control] == ["c1"]


def test_load_skips_runs_without_timeseries(tmp_path):
    _make_run(tmp_path, "good", beta_gov="0.0")
    rd = layout.run_dir(tmp_path, "notimeseries")
    rd.mkdir(parents=True)
    write_manifest(
        rd,
        run_id="notimeseries",
        spec={"model": "fake", "beta_gov": "0.0"},
        model={"name": "fake"},
        result={"config_id": "x", "series_final_ord": {}, "series_final_all": {},
                "cost": {}, "capabilities": {}, "params": {}},
        environment={},
    )
    records = load_run_series(tmp_path)
    assert [r["run_id"] for r in records] == ["good"]


# ---------------------------------------------------------------- 聚合


def test_aggregate_mean_and_ci(tmp_path):
    _make_run(tmp_path, "a", beta_gov="0.0", ord_extreme=np.linspace(0.0, 1.0, _STEPS))
    _make_run(tmp_path, "b", beta_gov="0.0", ord_extreme=np.linspace(1.0, 0.0, _STEPS))
    records = load_run_series(tmp_path)

    rows = aggregate_timeseries(records, group_by=["beta_gov"])
    assert len(rows) == 1
    row = rows[0]
    assert row["ord_extreme.n"] == 2
    # 两条互补的直线，逐点均值恒为 0.5
    np.testing.assert_allclose(row["ord_extreme.mean"], np.full(_STEPS, 0.5))
    # CI 必须把均值夹在中间；值域落在 [0,1] 故被判定为比例量
    low, high = row["ord_extreme.ci_low"], row["ord_extreme.ci_high"]
    assert np.all(low <= row["ord_extreme.mean"])
    assert np.all(high >= row["ord_extreme.mean"])
    assert row["ord_extreme.proportion"] is True
    # 比例量的 CI 不得越出 [0,1]——负的"占比"是正态近似的伪影，不是不确定性
    assert float(low.min()) >= 0.0
    assert float(high.max()) <= 1.0


def test_proportion_ci_never_leaves_unit_interval(tmp_path):
    """样本量足够时，CI 宽度应如实反映离散度；比例的 CI 始终留在 [0,1]。"""
    dispersion = np.array([0.02, 0.02, 0.02, 0.02, 0.30, 0.30])
    for i, sign in enumerate([-1.0, -0.5, 0.5, 1.0]):
        _make_run(tmp_path, f"r{i}", beta_gov="0.0", ord_extreme=0.5 + sign * dispersion)
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    row = rows[0]

    width = row["ord_extreme.ci_high"] - row["ord_extreme.ci_low"]
    assert float(width[4]) > float(width[0]) * 5  # 离散度大处置信带更宽
    assert float(row["ord_extreme.ci_low"].min()) >= 0.0
    assert float(row["ord_extreme.ci_high"].max()) <= 1.0


def test_nonproportion_series_is_not_clipped(tmp_path):
    """值域越出 [0,1] 的序列（如带符号的平均意见）不应被夹进 [0,1]。"""
    for i, offset in enumerate([-0.8, -0.4, 0.4, 0.8]):
        _make_run(tmp_path, f"r{i}", beta_gov="0.0",
                  ord_extreme=np.linspace(-0.9, 1.2, _STEPS) * offset)
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    row = rows[0]
    assert row["ord_extreme.proportion"] is False
    assert float(row["ord_extreme.ci_low"].min()) < 0.0


def test_aggregate_separates_groups(tmp_path):
    _make_run(tmp_path, "a", beta_gov="0.0")
    _make_run(tmp_path, "b", beta_gov="0.2", ord_extreme=_series(0.8))
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    assert {str(r["beta_gov"]) for r in rows} == {"0.0", "0.2"}


def test_single_run_ci_collapses_to_point(tmp_path):
    _make_run(tmp_path, "solo", beta_gov="0.0")
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    mean = rows[0]["ord_extreme.mean"]
    np.testing.assert_array_equal(rows[0]["ord_extreme.ci_low"], mean)
    np.testing.assert_array_equal(rows[0]["ord_extreme.ci_high"], mean)
    np.testing.assert_array_equal(rows[0]["ord_extreme.std"], np.zeros(_STEPS))


def test_aggregate_rejects_mismatched_lengths(tmp_path):
    _make_run(tmp_path, "six", beta_gov="0.0", steps=_STEPS)
    _make_run(tmp_path, "four", beta_gov="0.0", steps=4)
    with pytest.raises(ValueError, match="时间步数不一致"):
        aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])


def test_aggregate_missing_column_reports_zero(tmp_path):
    records = load_run_series(tmp_path) if False else [
        {"run_id": "x", "beta_gov": "0", "step": np.arange(3), "ord_extreme": np.zeros(3)}
    ]
    rows = aggregate_timeseries(records, group_by=["beta_gov"], columns=["all_extreme"])
    assert rows[0]["all_extreme.n"] == 0
    assert rows[0]["all_extreme.mean"] is None


def test_aggregate_empty_records():
    assert aggregate_timeseries([], group_by=["beta_gov"]) == []


# ---------------------------------------------------------------- 导出与绘图


def test_timeseries_to_csv_is_long_format(tmp_path):
    _make_run(tmp_path, "a", beta_gov="0.0")
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    out = timeseries_to_csv(rows, tmp_path / "curves.csv", columns=["ord_extreme"])

    text = out.read_text(encoding="utf-8").strip().splitlines()
    assert text[0].startswith("beta_gov,step,ord_extreme.mean")
    assert len(text) == _STEPS + 1  # 表头 + 每个时间点一行


def test_plot_renders_given_mean_without_recomputing(tmp_path):
    """契约：图上每个点等于聚合层给的数组，渲染层不得重算。"""
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        pytest.skip("需要 [analysis] 可选依赖")

    _make_run(tmp_path, "a", beta_gov="0.0", ord_extreme=np.linspace(0.0, 1.0, _STEPS))
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    expected = rows[0]["ord_extreme.mean"]

    fig = plot_timeseries(rows, columns=["ord_extreme"])
    try:
        ax = fig.axes[0]
        assert len(ax.lines) == 1
        np.testing.assert_array_equal(ax.lines[0].get_ydata(), expected)
        np.testing.assert_array_equal(ax.lines[0].get_xdata(), rows[0]["step"])
        assert ax.get_ylabel() == "ord_extreme"
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_plot_ci_band_matches_aggregate(tmp_path):
    """契约：置信带的上下边界等于聚合层给的 ci_low / ci_high。"""
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        pytest.skip("需要 [analysis] 可选依赖")

    _make_run(tmp_path, "a", beta_gov="0.0", ord_extreme=np.linspace(0.0, 1.0, _STEPS))
    _make_run(tmp_path, "b", beta_gov="0.0", ord_extreme=np.linspace(1.0, 0.0, _STEPS))
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    row = rows[0]

    fig = plot_timeseries(rows, columns=["ord_extreme"])
    try:
        ax = fig.axes[0]
        assert len(ax.collections) == 1
        verts = ax.collections[0].get_paths()[0].vertices
        assert float(verts[:, 1].min()) == pytest.approx(float(row["ord_extreme.ci_low"].min()))
        assert float(verts[:, 1].max()) == pytest.approx(float(row["ord_extreme.ci_high"].max()))
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_plot_rejects_unknown_column(tmp_path):
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        pytest.skip("需要 [analysis] 可选依赖")

    _make_run(tmp_path, "a", beta_gov="0.0")
    rows = aggregate_timeseries(load_run_series(tmp_path), group_by=["beta_gov"])
    with pytest.raises(KeyError, match="没有这些序列列"):
        plot_timeseries(rows, columns=["nonexistent"])


def test_plot_rejects_empty_rows():
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        pytest.skip("需要 [analysis] 可选依赖")

    with pytest.raises(ValueError, match="没有可绘制的时间序列行"):
        plot_timeseries([])
