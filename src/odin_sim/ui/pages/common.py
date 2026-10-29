"""UI 页面共享的 sweep 目录选择器与导出辅助（只读，不触发计算）。

从 ``runs`` 页抽出的公共逻辑：文本输入 + 自动发现 ``results/`` 下的 sweep，
以及把交互图/聚合表导出为 HTML/CSV（无需 kaleido）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from odin_sim.storage import layout
from odin_sim.ui.pages.runs import discover_sweeps
from odin_sim.ui import plotly_utils

DEFAULT_DIR = "results/ts_demo"

# 横轴 / 分组候选键（与 results.csv 列对应，按研究常用顺序）。
CANDIDATE_KEYS = ["mode", "placement", "scenario", "beta_gov", "tau", "config_id"]


def pick_sweep_dirs(base: str = "results") -> list[Path]:
    """多选 sweep 目录（从 ``base`` 下自动发现 + 可选附加自定义目录）。

    返回含 ``sweep_manifest.json`` 的有效目录列表；少于 2 个时对比无意义。
    """
    candidates = discover_sweeps(base)
    options = [str(c) for c in candidates]
    selected = st.multiselect(
        "选择要对比的 sweep（可多选）",
        options,
        default=options[:2] if len(options) >= 2 else options,
        help="从 results/ 下检测到的 sweep 中选择；至少 2 个才有对比意义。",
    )
    extra = st.text_input(
        "附加 sweep 目录（可选，绝对或相对路径）",
        value="",
        help="指向其他位置的 sweep 根目录（需含 sweep_manifest.json）。",
    )
    roots: list[Path] = [Path(s) for s in selected]
    if extra.strip():
        p = Path(extra.strip())
        if p.is_dir() and layout.sweep_manifest_path(p).is_file():
            roots.append(p)
        else:
            st.warning(f"附加目录无效（需含 sweep_manifest.json）：{p}")
    return [r for r in roots if layout.sweep_manifest_path(r).is_file()]


def pick_sweep_dir(default: str = DEFAULT_DIR) -> Path | None:
    """返回一个有效 sweep 目录（含 sweep_manifest.json），无效时返回 None。"""
    raw = st.text_input(
        "Sweep 目录",
        value=default,
        help="包含 sweep_manifest.json 的目录；无效时会列出 results/ 下已检测到的 sweep。",
    )
    root = Path(raw)
    if root.is_dir() and layout.sweep_manifest_path(root).is_file():
        return root

    candidates = discover_sweeps("results")
    if candidates:
        st.info(f"“{raw}” 不是有效 sweep 目录，已从 results/ 检测到以下 sweep：")
        choice = st.selectbox("选择一个 sweep", [str(c) for c in candidates])
        return Path(choice)

    st.warning(f"未找到 sweep_manifest.json：{layout.sweep_manifest_path(root)}")
    return None


def export_figure_and_table(
    fig, df: pd.DataFrame, prefix: str, label: str = "图"
) -> None:
    """导出交互图（HTML，无需 kaleido）与聚合表（CSV）。纯只读，不重算。

    两列布局并排两个下载按钮；``prefix`` 用于文件命名与 st key 去重。
    """
    html = plotly_utils.figure_to_html(fig)
    left, right = st.columns(2)
    left.download_button(
        f"下载{label} (HTML)",
        html,
        file_name=f"{prefix}_figure.html",
        mime="text/html",
        key=f"dl_{prefix}_html",
    )
    right.download_button(
        f"下载聚合表 (CSV)",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"{prefix}_aggregate.csv",
        mime="text/csv",
        key=f"dl_{prefix}_csv",
    )
