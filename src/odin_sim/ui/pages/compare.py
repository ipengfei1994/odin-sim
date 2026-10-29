"""P2-2：多 sweep 对比（只读，复用 analysis.aggregate）。

选择 2+ 个 sweep，按共享横轴键（如 beta_gov）对齐，叠加各 sweep 的某指标均值
并分色。数据全部来自已落盘的 results.csv，经 ``analysis.aggregate`` 聚合，
不重跑仿真、不引入新的动力学逻辑。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from odin_sim.analysis import aggregate, filter_by_role, load_results
from odin_sim.ui import plotly_utils
from odin_sim.ui.pages import runs as runs_page
from odin_sim.ui.pages.common import (
    CANDIDATE_KEYS,
    pick_sweep_dirs,
    export_figure_and_table,
)


def _sweep_label(root: Path) -> str:
    try:
        data = runs_page.load_sweep_manifest(root)
        name = data.get("name")
        if name:
            return str(name)
    except (OSError, ValueError):
        pass
    return root.name


def render() -> None:
    st.header("多 sweep 对比")
    st.caption("跨多个 sweep 叠加同一指标；复用 analysis.aggregate，不重跑仿真。")

    roots = pick_sweep_dirs()
    if len(roots) < 2:
        st.info("请至少选择 2 个 sweep 进行对比（在上方多选中勾选）。")
        return

    # 以第一个有效 sweep 的 results.csv 决定候选指标 / 横轴键。
    first_df = runs_page.load_results_df(roots[0])
    if first_df is None:
        st.warning(f"{roots[0]} 没有 results.csv，无法对比。")
        return
    numeric_cols = [c for c in first_df.columns if pd.api.types.is_numeric_dtype(first_df[c])]
    key_cols = [c for c in CANDIDATE_KEYS if c in first_df.columns]

    metric = st.selectbox(
        "指标", numeric_cols, index=_default_metric_index(numeric_cols)
    )
    x_key = st.selectbox("横轴", key_cols, index=_default_index(key_cols, "beta_gov"))
    confidence = st.slider("置信度", 0.80, 0.99, 0.95, 0.01)

    rows: list[dict] = []
    skipped: list[str] = []
    for root in roots:
        df = runs_page.load_results_df(root)
        if df is None or x_key not in df.columns:
            skipped.append(str(root))
            continue
        results = filter_by_role(load_results(root), "main")
        if not results:
            skipped.append(str(root))
            continue
        agg = aggregate(results, [x_key], [metric], confidence=confidence)
        for r in agg:
            r2 = dict(r)
            r2["sweep"] = _sweep_label(root)
            rows.append(r2)

    if skipped:
        st.warning("以下 sweep 跳过（缺 results.csv / 横轴列 / 可聚合结果）：" + "；".join(skipped))
    if not rows:
        st.error("没有可对比的数据。")
        return

    fig = plotly_utils.compare_figure(rows, x_key, metric, sweep_key="sweep")
    st.plotly_chart(fig, width="stretch")

    compare_df = pd.DataFrame(rows)
    export_figure_and_table(fig, compare_df, prefix="compare", label="对比")

    st.subheader("对比表")
    st.dataframe(compare_df, width="stretch")


def _default_metric_index(numeric_cols: list[str]) -> int:
    preferred = ["ord_extreme_final", "all_extreme_final", "ord_joint_final"]
    for p in preferred:
        if p in numeric_cols:
            return numeric_cols.index(p)
    return 0


def _default_index(options: list[str], preferred: str) -> int:
    return options.index(preferred) if preferred in options else 0
