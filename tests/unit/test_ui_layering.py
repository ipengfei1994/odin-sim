"""L4 分层不变量：不得有 ``ui`` / ``api`` 之外的模块 import 它们。

对应 docs/architecture-v1.md §4.3 与 docs/ui-plan.md §8 验收标准。``ui`` 与
``api`` 都是纯展示/接口层，核心层（models/metrics/topology/state）、实验层、
分析层、CLI 都只应消费其产物，不得反向 import。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "odin_sim"
L4_PKGS = (SRC / "ui", SRC / "api")


def _source_files():
    for p in SRC.rglob("*.py"):
        if any((pkg in p.parents or p.parent == pkg) for pkg in L4_PKGS):
            continue
        yield p


def test_no_core_module_imports_l4_layers():
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
                if m.startswith("odin_sim.ui") or m.startswith("odin_sim.api"):
                    violations.append(p.relative_to(SRC.parent))
                    break
    assert not violations, f"发现反向依赖 L4 接口的模块：{violations}"


def test_ui_package_imports_without_streamlit():
    # 顶层 import odin_sim.ui 不应触发 streamlit（保证不装 [ui] 时也能 import）。
    import odin_sim.ui

    assert hasattr(odin_sim.ui, "APP_PATH")
    assert odin_sim.ui.APP_PATH.name == "app.py"
