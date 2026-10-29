"""P1-5a：结果可视化（plotly 交互）。

复用 ``analysis.aggregate`` 做跨条件聚合（不重算任何统计量），
前端 plotly 交互渲染。只读 results.csv，不重跑仿真。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from odin_sim.analysis import aggregate, filter_by_role, load_results
from odin_sim.ui import plotly_utils
from odin_sim.ui.pages import runs as runs_page
from odin_sim.ui.pages.common import (
    CANDIDATE_KEYS,
    pick_sweep_dir,
    export_figure_and_table,
)


def render() -> None:
    st.header("可视化")
    st.caption("复用 analysis 聚合，plotly 交互渲染；不重跑仿真。")

    root = pick_sweep_dir()
    if root is None:
        return

    df = runs_page.load_results_df(root)
    if df is None:
        st.info("results.csv 不存在（该 sweep 可能尚未汇总）。")
        return

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    key_cols = [c for c in CANDIDATE_KEYS if c in df.columns]

    metrics = st.multiselect(
        "指标（数值列）",
        numeric_cols,
        default=_default_metrics(numeric_cols),
        help="从 results.csv 的数值列中选择要画对比的指标。",
    )
    if not metrics:
        st.info("请至少选择一个指标。")
        return

    x_key = st.selectbox("横轴", key_cols, index=_default_index(key_cols, "beta_gov"))
    hue_key = st.selectbox(
        "分组（可选）", ["（无）", *[k for k in key_cols if k != x_key]]
    )
    confidence = st.slider("置信度", 0.80, 0.99, 0.95, 0.01)

    results = filter_by_role(load_results(root), "main")
    group_by = [x_key] + ([hue_key] if hue_key != "（无）" else [])
    rows = aggregate(results, group_by, metrics, confidence=confidence)
    if not rows:
        st.warning("聚合结果为空（可能该角色下没有可聚合的行）。")
        return

    fig = plotly_utils.metric_figure(
        rows, x_key, metrics, hue_key if hue_key != "（无）" else None
    )
    st.plotly_chart(fig, width="stretch")

    agg_df = pd.DataFrame(rows)
    export_figure_and_table(fig, agg_df, prefix="explore")

    st.subheader("聚合表")
    st.dataframe(agg_df, width="stretch")


def _default_metrics(numeric_cols: list[str]) -> list[str]:
    preferred = ["ord_extreme_final", "all_extreme_final", "ord_joint_final"]
    return [c for c in preferred if c in numeric_cols][:2] or numeric_cols[:1]


def _default_index(options: list[str], preferred: str) -> int:
    return options.index(preferred) if preferred in options else 0
