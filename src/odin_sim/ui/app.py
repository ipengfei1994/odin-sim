"""ODIN-Sim Streamlit 入口（L4 接口层）。

只在安装 ``[ui]`` 依赖、通过 ``odin-sim ui`` 启动时才被加载；本文件直接
import streamlit，故不应被核心层或无 UI 依赖的环境导入。导航用侧边栏
radio 分发到各 page 模块的 ``render()``。页面模块均为延迟导入，避免任一
页面依赖在仅加载 app 时即被触发。
"""
from __future__ import annotations

from importlib import import_module

import streamlit as st

# (导航标签, 页面模块路径) —— 顺序即侧边栏顺序。
_PAGES: list[tuple[str, str]] = [
    ("配置实验", "odin_sim.ui.pages.configure"),
    ("浏览结果", "odin_sim.ui.pages.runs"),
    ("可视化", "odin_sim.ui.pages.explore"),
    ("演化曲线", "odin_sim.ui.pages.timeseries"),
    ("多 sweep 对比", "odin_sim.ui.pages.compare"),
]


def main() -> None:
    st.set_page_config(page_title="ODIN-Sim", layout="wide")
    st.sidebar.title("ODIN-Sim")
    st.sidebar.caption("L4 接口层 · 只读已有产物，不重跑仿真")

    labels = [label for label, _ in _PAGES]
    choice = st.sidebar.radio("导航", labels, key="nav")

    module_name = next(mod for label, mod in _PAGES if label == choice)
    page = import_module(module_name)
    page.render()


if __name__ == "__main__":  # pragma: no cover
    main()
