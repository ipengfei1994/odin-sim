"""P2 测试：多 sweep 对比页 + 导出辅助（基于已落盘产物，不重跑仿真）。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from odin_sim.ui import plotly_utils

_TS = Path("results/ts_demo").resolve()
_VIZ = Path("results/viz_demo").resolve()


# --------------------------------------------------------------------------- #
# 纯函数测试（不依赖 streamlit）
# --------------------------------------------------------------------------- #
def test_figure_to_html_returns_plotly_div():
    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[1, 2], y=[3, 4], name="a"))
    html = plotly_utils.figure_to_html(fig)
    assert isinstance(html, str)
    assert "plotly" in html.lower()
    assert "<div" in html


def test_compare_figure_one_trace_per_sweep():
    rows = [
        {"beta_gov": 0.0, "sweep": "A", "m.mean": 0.1, "m.ci_low": 0.05, "m.ci_high": 0.15},
        {"beta_gov": 0.2, "sweep": "A", "m.mean": 0.2, "m.ci_low": 0.15, "m.ci_high": 0.25},
        {"beta_gov": 0.0, "sweep": "B", "m.mean": 0.3, "m.ci_low": 0.25, "m.ci_high": 0.35},
        {"beta_gov": 0.2, "sweep": "B", "m.mean": 0.4, "m.ci_low": 0.35, "m.ci_high": 0.45},
    ]
    fig = plotly_utils.compare_figure(rows, "beta_gov", "m", sweep_key="sweep")
    names = {t.name for t in fig.data}
    assert names == {"A", "B"}
    # 含 CI 时应带误差棒（error_y.array 非空）
    assert all(getattr(t.error_y, "array", None) is not None for t in fig.data)


def test_compare_figure_without_ci_no_error_y():
    rows = [
        {"beta_gov": 0.0, "sweep": "A", "m.mean": 0.1, "m.ci_low": None, "m.ci_high": None},
        {"beta_gov": 0.2, "sweep": "A", "m.mean": 0.2, "m.ci_low": None, "m.ci_high": None},
    ]
    fig = plotly_utils.compare_figure(rows, "beta_gov", "m", sweep_key="sweep")
    # plotly 的 error_y 默认存在但 array 为空；无 CI 时不应挂误差棒。
    assert all(getattr(t.error_y, "array", None) is None for t in fig.data)


# --------------------------------------------------------------------------- #
# 页面冒烟（AppTest，基于已落盘 products）
# --------------------------------------------------------------------------- #
def _render_compare(tmp_path: Path, wanted: list[str]):
    from streamlit.testing.v1 import AppTest

    script = tmp_path / "app.py"
    script.write_text(
        "from odin_sim.ui.pages.compare import render\nrender()\n", encoding="utf-8"
    )
    at = AppTest.from_file(str(script))
    at.run(timeout=15)
    assert len(at.exception) == 0
    # 从实际选项里按目录名挑，避免绝对/相对路径分隔符不匹配。
    opts = list(at.multiselect[0].options)
    chosen = [o for o in opts if any(w in o for w in wanted)]
    at.multiselect[0].set_value(chosen)
    at.run(timeout=15)
    assert len(at.exception) == 0
    return at


def test_compare_page_renders_two_sweeps(tmp_path):
    at = _render_compare(tmp_path, ["ts_demo", "viz_demo"])
    # 导出辅助应渲染 2 个下载按钮（图 HTML + 聚合表 CSV）
    assert len(at.download_button) >= 2


def test_compare_page_single_sweep_warns(tmp_path):
    at = _render_compare(tmp_path, ["ts_demo"])
    # 少于 2 个不应崩溃，给出提示
    assert len(at.exception) == 0
    joined = " ".join(str(e.value) for e in at.info)
    assert "至少" in joined or len(at.download_button) == 0
