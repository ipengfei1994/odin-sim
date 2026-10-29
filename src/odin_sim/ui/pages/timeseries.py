"""P1-5b：演化曲线（plotly，带置信带）。

复用 ``analysis.aggregate_timeseries`` 把逐时间步序列聚合成 mean/CI，
前端 plotly 渲染均值线 + 半透明置信带。只读 timeseries.csv，不重跑仿真。
"""
from __future__ import annotations

import pandas as pd
import numpy as np
import streamlit as st

from odin_sim.analysis import aggregate_timeseries, load_run_series
from odin_sim.ui import plotly_utils
from odin_sim.ui.pages.common import pick_sweep_dir, export_figure_and_table

STEP_COLUMN = "step"


def render() -> None:
    st.header("演化曲线")
    st.caption("复用 analysis 聚合逐时间步序列，plotly 带置信带渲染；不重跑仿真。")

    root = pick_sweep_dir()
    if root is None:
        return

    records = load_run_series(root, role="main")
    if not records:
        st.warning(
            "没有时间序列产物（runs/<id>/timeseries.csv）。\n"
            "时间序列总是随 sweep 落盘；若这批产物较早，重跑一次即可补写，"
            "无需开启 store_trajectory。"
        )
        return

    series_cols = [
        c for c in records[0]
        if c not in ("run_id", STEP_COLUMN) and isinstance(records[0][c], np.ndarray)
    ]
    group_keys = [
        c for c in records[0]
        if c not in ("run_id", STEP_COLUMN) and not isinstance(records[0][c], np.ndarray)
    ]

    columns = st.multiselect(
        "序列列", series_cols, default=series_cols[:2],
        help="模型的逐时间步序列输出（ord_* / all_*）。",
    )
    if not columns:
        st.info("请至少选择一个序列列。")
        return

    group_by = st.multiselect(
        "分组键（留空=所有运行并成一条曲线）", group_keys, default=[]
    )
    confidence = st.slider("置信度", 0.80, 0.99, 0.95, 0.01)

    try:
        rows = aggregate_timeseries(
            records, group_by, columns, confidence=confidence
        )
    except ValueError as exc:
        st.error(f"聚合失败：{exc}")
        return

    if not rows:
        st.warning("聚合结果为空。")
        return

    fig = plotly_utils.timeseries_figure(rows, columns, group_by)
    st.plotly_chart(fig, width="stretch")

    summary = [
        {**( {k: r.get(k, "") for k in group_by} if group_by else {"group": "all"}),
         "n": int(r[f"{columns[0]}.n"]) if columns else 0,
         "steps": int(np.asarray(r[STEP_COLUMN]).size)}
        for r in rows
    ]
    summary_df = pd.DataFrame(summary)
    export_figure_and_table(fig, summary_df, prefix="timeseries", label="曲线")

    st.subheader("分组概览")
    st.dataframe(summary_df, width="stretch")
