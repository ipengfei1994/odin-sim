"""P1-3 runs 页冒烟测试。

纯函数（不依赖 streamlit）始终可测；整页渲染用 streamlit 的 AppTest，
无 [ui] 依赖时整体跳过。全部基于已落盘的 results/ts_demo，不重跑仿真。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TS_DEMO = ROOT / "results" / "ts_demo"

import pytest

pytest.importorskip("streamlit")  # 无 UI 依赖时跳过整页测试

from odin_sim.ui.pages import runs as runs_page


def test_discover_sweeps_finds_ts_demo():
    found = runs_page.discover_sweeps(ROOT / "results")
    assert TS_DEMO in found


def test_load_sweep_manifest_contract():
    data = runs_page.load_sweep_manifest(TS_DEMO)
    assert data["name"] == "ts_demo"
    assert data["counts"]["total"] == 4
    assert data["spec"]["model"] == "g3-baseline-v1"
    assert "beta_gov" in data["spec"]["axes"]


def test_load_results_df_shape():
    df = runs_page.load_results_df(TS_DEMO)
    assert df is not None
    assert len(df) == 4
    for col in ("run_id", "beta_gov", "ord_extreme_final", "status"):
        assert col in df.columns


def test_iter_run_manifests():
    paths = runs_page.iter_run_manifests(TS_DEMO)
    assert len(paths) == 4
    # 全部是 runs/<id>/manifest.json
    assert all(p.parent.parent.name == "runs" for p in paths)


def test_page_renders_without_error(tmp_path):
    from streamlit.testing.v1 import AppTest

    # from_function 在隔离命名空间执行函数体，丢失模块的 `import streamlit as st`；
    # 用临时 shim 脚本正常 import 模块，全局命名空间正确。
    script = tmp_path / "app.py"
    script.write_text(
        "from odin_sim.ui.pages.runs import render\nrender()\n",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(script))
    at.run()  # 首次运行注册控件
    at.text_input[0].set_value(str(TS_DEMO))
    at.run()  # 以新目录重跑
    assert len(at.exception) == 0  # AppTest 无异常时返回空 ElementList，非 None
    # 汇总表（results.csv）已渲染
    assert len(at.dataframe) >= 1
