"""UI 页面共享的 sweep 目录选择器（只读，不触发计算）。

从 ``runs`` 页抽出的公共逻辑：文本输入 + 自动发现 ``results/`` 下的 sweep。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from odin_sim.storage import layout
from odin_sim.ui.pages.runs import discover_sweeps

DEFAULT_DIR = "results/ts_demo"


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
