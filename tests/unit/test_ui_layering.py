"""L4 分层不变量：除 ``ui`` 包自身外，不得有模块 import ``odin_sim.ui``。

对应 docs/architecture-v1.md §4.3 与 docs/ui-plan.md §8 验收标准。UI 是纯展示层，
核心层（models/metrics/topology/state）、实验层、分析层、CLI 都只应消费其产物或
经 path 定位 app.py，不得直接 import 本包。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "odin_sim"
UI_PKG = SRC / "ui"


def _source_files():
    for p in SRC.rglob("*.py"):
        if UI_PKG in p.parents or p.parent == UI_PKG:
            continue
        yield p


def test_no_core_module_imports_ui():
    violations = []
    for p in _source_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            for m in mods:
                if m == "odin_sim.ui" or m.startswith("odin_sim.ui."):
                    violations.append(p.relative_to(SRC.parent))
                    break
    assert not violations, f"发现反向依赖 odin_sim.ui 的模块：{violations}"


def test_ui_package_imports_without_streamlit():
    # 顶层 import odin_sim.ui 不应触发 streamlit（保证不装 [ui] 时也能 import）。
    import odin_sim.ui

    assert hasattr(odin_sim.ui, "APP_PATH")
    assert odin_sim.ui.APP_PATH.name == "app.py"
