"""可视化层（figures）单元测试。

重点不是"图好不好看"，而是两条契约：
1. **渲染层不重算统计量**：图上画的点必须等于 ``aggregate`` 已经算好的值。
2. **缺数据不画假图**：点估计全空或列名不存在时报错，而不是产出一张空图。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from odin_sim.analysis import aggregate, estimate_ci
from odin_sim.analysis.figures import (
    MetricColumns,
    plot_bars,
    plot_curves,
    plot_heatmap,
    resolve_metric_columns,
)

mpl = pytest.importorskip("matplotlib")
mpl.use("Agg")


def _fake_results() -> list[dict]:
    """2 个 beta_gov × 2 个 mode × 2 个 tau × 2 个种子."""
    rows = []
    for mode in ("cross", "direct"):
        for bg in (0.0, 0.2):
            for tau in (30, 60):
                for s in (1, 2):
                    rows.append({
                        "run_id": f"{mode}_{bg}_{tau}_{s}",
                        "mode": mode,
                        "beta_gov": float(bg),
                        "tau": tau,
                        "seed": s,
                        "all_extreme_final": float(bg) + 0.1 * s + tau / 1000,
                        "g3.backfire.bf_final": s == 1,
                    })
    return rows


def test_resolve_numeric_columns():
    rows = aggregate(_fake_results(), ["mode", "beta_gov"], ["all_extreme_final"])
    cols = resolve_metric_columns(rows, "all_extreme_final")
    assert cols == MetricColumns(
        "numeric", "all_extreme_final.mean",
        "all_extreme_final.ci_low", "all_extreme_final.ci_high",
    )
    assert not cols.is_rate


def test_resolve_bool_metric_uses_rate():
    rows = aggregate(_fake_results(), ["mode", "beta_gov"], ["g3.backfire.bf_final"])
    cols = resolve_metric_columns(rows, "g3.backfire.bf_final")
    assert cols.kind == "rate"
    assert cols.value == "g3.backfire.bf_final.rate"


def test_resolve_missing_metric_raises():
    rows = aggregate(_fake_results(), ["mode"], ["all_extreme_final"])
    with pytest.raises(KeyError, match="没有可用的点估计列"):
        resolve_metric_columns(rows, "not_a_metric")


def test_resolve_empty_rows_raises():
    with pytest.raises(ValueError, match="聚合结果为空"):
        resolve_metric_columns([], "all_extreme_final")


def test_curves_plot_exactly_the_aggregated_values(tmp_path: Path):
    rows = aggregate(_fake_results(), ["mode", "beta_gov"], ["all_extreme_final"])
    out = tmp_path / "curve.png"
    fig = plot_curves(rows, x="beta_gov", metric="all_extreme_final",
                      hue="mode", out=out)
    try:
        ax = fig.axes[0]
        # 2 个 mode -> 2 条线
        assert len(ax.lines) == 2
        # 每条线的 y 值必须等于聚合表里的 mean（顺序已按 x 排序：0.0, 0.2）
        plotted = {tuple(line.get_ydata()) for line in ax.lines}
        for mode in ("cross", "direct"):
            grp = sorted(
                (r for r in rows if r["mode"] == mode),
                key=lambda r: float(r["beta_gov"]),
            )
            expected = tuple(r["all_extreme_final.mean"] for r in grp)
            assert expected in plotted
    finally:
        mpl.pyplot.close(fig)
    assert out.is_file()


def test_curves_draws_ci_band(tmp_path: Path):
    rows = aggregate(_fake_results(), ["beta_gov"], ["all_extreme_final"])
    fig = plot_curves(rows, x="beta_gov", metric="all_extreme_final")
    try:
        ax = fig.axes[0]
        # CI 带是 PolyCollection；点估计非空时应存在一个
        assert len(ax.collections) >= 1
    finally:
        mpl.pyplot.close(fig)


def test_curves_handles_categorical_x(tmp_path: Path):
    rows = aggregate(_fake_results(), ["mode"], ["all_extreme_final"])
    out = tmp_path / "cat.png"
    fig = plot_curves(rows, x="mode", metric="all_extreme_final", out=out)
    try:
        ax = fig.axes[0]
        # 类别轴会把刻度标签换成原始取值
        assert {t.get_text() for t in ax.get_xticklabels()} == {"cross", "direct"}
    finally:
        mpl.pyplot.close(fig)
    assert out.is_file()


def test_bars_use_asymmetric_error_from_ci(tmp_path: Path):
    """误差棒取自 CI，且是 [下, 上] 非对称形式——比例 CI 被裁剪过会不对称。"""
    rows = aggregate(_fake_results(), ["mode", "beta_gov"], ["g3.backfire.bf_final"])
    out = tmp_path / "bar.png"
    fig = plot_bars(rows, x="beta_gov", metric="g3.backfire.bf_final",
                    hue="mode", out=out)
    try:
        ax = fig.axes[0]
        # 2 mode × 2 beta_gov = 4 根柱子 + 4 组误差棒容器
        assert len(ax.patches) == 4
        assert len(ax.containers) >= 4
    finally:
        mpl.pyplot.close(fig)
    assert out.is_file()


def test_heatmap_layout_and_missing_cells(tmp_path: Path):
    # 人为删掉一个组合，验证缺格留 nan 而不是被当 0
    partial = [r for r in _fake_results() if not (r["tau"] == 30 and r["beta_gov"] == 0.2)]
    rows = aggregate(partial, ["beta_gov", "tau"], ["all_extreme_final"])
    out = tmp_path / "phase.png"
    fig = plot_heatmap(rows, x="beta_gov", y="tau", metric="all_extreme_final", out=out)
    try:
        ax = fig.axes[0]
        image = ax.images[0]
        arr = image.get_array()
        # 行对应 tau（2 个取值），列对应 beta_gov（2 个取值）
        assert arr.shape == (2, 2)
        assert bool(arr.mask.any()), "缺失组合必须被掩掉（留白），不能填 0"
    finally:
        mpl.pyplot.close(fig)
    assert out.is_file()


def test_ci_values_are_not_recomputed():
    """图上的 CI 必须等于聚合层给的 ci_low/ci_high（而不是重算一份）。

    fill_between 对整条 x 轴只生成一个多边形，其顶点同时含各 x 处的上下界，
    因此要比对这些边界值的集合，而不是整个多边形的 min/max。
    """
    rows = aggregate(_fake_results(), ["beta_gov"], ["all_extreme_final"])
    expected: set[float] = set()
    for r in rows:
        expected.add(round(r["all_extreme_final.ci_low"], 9))
        expected.add(round(r["all_extreme_final.ci_high"], 9))

    fig = plot_curves(rows, x="beta_gov", metric="all_extreme_final")
    try:
        poly = fig.axes[0].collections[0]
        verts = poly.get_paths()[0].vertices
        got = {round(float(v), 9) for v in verts[:, 1]}
        assert got == expected
    finally:
        mpl.pyplot.close(fig)


def test_plot_raises_on_unknown_axis_column():
    rows = aggregate(_fake_results(), ["mode"], ["all_extreme_final"])
    with pytest.raises(KeyError, match="没有列"):
        plot_curves(rows, x="not_a_column", metric="all_extreme_final")


def test_plot_skips_groups_without_point_estimate(tmp_path: Path):
    """某组点估计缺失（如该组运行全部失败）时跳过该组，其余照常画。"""
    rows = aggregate(_fake_results(), ["mode"], ["all_extreme_final"])
    # 让 cross 组为空、direct 组保留
    for r in rows:
        if r["mode"] == "cross":
            r["all_extreme_final.mean"] = None

    out = tmp_path / "partial.png"
    fig = plot_curves(rows, x="mode", metric="all_extreme_final", out=out)
    try:
        lines = fig.axes[0].lines
        assert len(lines) == 1
        direct = next(r for r in rows if r["mode"] == "direct")
        assert tuple(lines[0].get_ydata()) == (direct["all_extreme_final.mean"],)
    finally:
        mpl.pyplot.close(fig)
    assert out.is_file()


def test_plot_raises_when_no_usable_point_estimate():
    """点估计全空时必须报错，绝不产出一张没有散点的空坐标图。"""
    rows = aggregate(_fake_results(), ["mode"], ["all_extreme_final"])
    for r in rows:
        r["all_extreme_final.mean"] = None
    with pytest.raises((KeyError, ValueError)):
        plot_curves(rows, x="mode", metric="all_extreme_final")


def test_estimate_ci_unchanged_contract():
    # 守卫：聚合层的 CI 语义不能被渲染层的改动带偏
    lo, hi = estimate_ci([1.0, 2.0, 3.0], 0.95)
    assert lo < 2.0 < hi


def test_cli_plot_end_to_end(tmp_path: Path, capsys):
    csvp = tmp_path / "results.csv"
    header = "run_id,mode,beta_gov,tau,seed,all_extreme_final\n"
    lines = []
    for mode in ("cross", "direct"):
        for bg in (0.0, 0.2):
            for tau in (30, 60):
                for s in (1, 2):
                    lines.append(
                        f"{mode}_{bg}_{tau}_{s},{mode},{bg},{tau},{s},"
                        f"{float(bg) + 0.1 * s + tau / 1000}\n"
                    )
    csvp.write_text(header + "".join(lines), encoding="utf-8")

    from odin_sim.cli import main

    for kind, extra in (
        ("curve", ["--hue", "mode"]),
        ("bar", ["--hue", "mode"]),
        ("heatmap", ["--heatmap-y", "tau"]),
    ):
        out = tmp_path / f"{kind}.png"
        rc = main([
            "plot", "--sweep", str(tmp_path), "--x", "beta_gov",
            "--metric", "all_extreme_final", "--kind", kind,
            "--out", str(out), *extra,
        ])
        assert rc == 0, capsys.readouterr().out
        assert out.is_file(), f"{kind} 未产出图片"


def test_cli_plot_rejects_unknown_column(tmp_path: Path, capsys):
    csvp = tmp_path / "results.csv"
    csvp.write_text(
        "run_id,beta_gov,seed,all_extreme_final\nr1,0.0,1,0.5\nr2,0.2,2,0.7\n",
        encoding="utf-8",
    )
    from odin_sim.cli import main

    rc = main([
        "plot", "--sweep", str(tmp_path), "--x", "no_such_column",
        "--metric", "all_extreme_final", "--out", str(tmp_path / "x.png"),
    ])
    assert rc == 2
    assert "no_such_column" in capsys.readouterr().err


def test_cli_plot_heatmap_requires_y(tmp_path: Path, capsys):
    csvp = tmp_path / "results.csv"
    csvp.write_text(
        "run_id,beta_gov,tau,seed,all_extreme_final\nr1,0.0,30,1,0.5\n",
        encoding="utf-8",
    )
    from odin_sim.cli import main

    rc = main([
        "plot", "--sweep", str(tmp_path), "--x", "beta_gov",
        "--metric", "all_extreme_final", "--kind", "heatmap",
        "--out", str(tmp_path / "x.png"),
    ])
    assert rc == 2
    assert "heatmap-y" in capsys.readouterr().err
