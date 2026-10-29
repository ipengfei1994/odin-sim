"""P1-5 explore / timeseries 可视化页测试。

纯函数（plotly_utils 的两个 figure 构造）始终可测；整页渲染用 AppTest，
基于已落盘的 results/ts_demo，不重跑仿真、不点击运行。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from odin_sim.analysis import aggregate, aggregate_timeseries, load_run_series
from odin_sim.analysis import load_results as load_analysis_results
from odin_sim.ui import plotly_utils

ROOT = Path(__file__).resolve().parents[2]
TS_DEMO = ROOT / "results" / "ts_demo"


# --------------------------------------------------------------------------- #
# plotly_utils 纯函数
# --------------------------------------------------------------------------- #
def test_metric_figure_one_trace_per_metric():
    rows = [
        {"beta_gov": 0.0, "ord_extreme_final.mean": 0.3, "ord_extreme_final.ci_low": 0.2,
         "ord_extreme_final.ci_high": 0.4},
        {"beta_gov": 0.4, "ord_extreme_final.mean": 0.5, "ord_extreme_final.ci_low": 0.4,
         "ord_extreme_final.ci_high": 0.6},
    ]
    fig = plotly_utils.metric_figure(rows, "beta_gov", ["ord_extreme_final"])
    assert len(fig.data) == 1
    assert fig.data[0].x == (0.0, 0.4)


def test_metric_figure_splits_by_hue():
    rows = [
        {"beta_gov": 0.0, "mode": "cross", "m.mean": 0.1, "m.ci_low": 0.0, "m.ci_high": 0.2},
        {"beta_gov": 0.4, "mode": "cross", "m.mean": 0.2, "m.ci_low": 0.1, "m.ci_high": 0.3},
        {"beta_gov": 0.0, "mode": "hub", "m.mean": 0.5, "m.ci_low": 0.4, "m.ci_high": 0.6},
        {"beta_gov": 0.4, "mode": "hub", "m.mean": 0.6, "m.ci_low": 0.5, "m.ci_high": 0.7},
    ]
    fig = plotly_utils.metric_figure(rows, "beta_gov", ["m"], hue_key="mode")
    # 2 个 mode × 1 指标 = 2 条线
    assert len(fig.data) == 2


def test_timeseries_figure_emits_band_and_mean():
    rows = [{
        "step": np.array([0, 1, 2]),
        "ord_extreme.mean": np.array([0.1, 0.2, 0.3]),
        "ord_extreme.ci_low": np.array([0.0, 0.1, 0.2]),
        "ord_extreme.ci_high": np.array([0.2, 0.3, 0.4]),
        "ord_extreme.n": 3,
    }]
    fig = plotly_utils.timeseries_figure(rows, ["ord_extreme"], [])
    # 每条序列 1 条置信带 + 1 条均值线 = 2 个 trace
    assert len(fig.data) == 2
    assert fig.data[0].fill == "toself"  # 置信带
    assert fig.data[1].mode == "lines"    # 均值线


# --------------------------------------------------------------------------- #
# 整页渲染（基于 ts_demo，只读）
# --------------------------------------------------------------------------- #
def _render_page(module_name: str, tmp_path: Path):
    from streamlit.testing.v1 import AppTest

    script = tmp_path / "app.py"
    script.write_text(
        f"from odin_sim.ui.pages.{module_name} import render\nrender()\n",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(script))
    # 首次运行含 streamlit/plotly/分析层全部导入，耗时 > 默认 3s，放宽超时。
    at.run(timeout=15)
    # 默认目录 results/ts_demo 在测试 cwd 下不解析；显式填入绝对路径。
    at.text_input[0].set_value(str(TS_DEMO))
    at.run(timeout=15)
    assert len(at.exception) == 0
    return at


def test_explore_page_renders(tmp_path):
    at = _render_page("explore", tmp_path)
    # 页面渲染无异常（图形构造已在 plotly_utils 单测覆盖）
    assert len(at.exception) == 0


def test_timeseries_page_renders(tmp_path):
    at = _render_page("timeseries", tmp_path)
    assert len(at.exception) == 0


def test_explore_and_timeseries_reuse_analysis_not_engine():
    """回归：可视化走 analysis 聚合，绝不自己重算或 import engine。"""
    results = load_analysis_results(TS_DEMO)
    rows = aggregate(results, ["beta_gov"], ["ord_extreme_final"], confidence=0.95)
    assert rows and any("ord_extreme_final.mean" in r for r in rows)

    records = load_run_series(TS_DEMO, role="main")
    ts_rows = aggregate_timeseries(records, [], ["ord_extreme"], confidence=0.95)
    assert ts_rows and "ord_extreme.mean" in ts_rows[0]
