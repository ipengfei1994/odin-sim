"""内核无关的端到端闭环回归：配置 → 运行 → 浏览 → 可视化 → 演化曲线 → 导出。

参考模型 ``reference-v1`` 是纯 Python、零外部依赖的动力学 stand-in，因此这条闭环
**不需要 G3 内核**即可运行。它锁定的是「平台工程正确性」这一不变量：
产物 schema、聚合口径、UI 各页的读取与渲染，都不依赖具体内核实现。

配套说明：参考模型数值为合成占位，不能当作科学结论；真实物理等价性由 G3
指纹回归（``test_g3_*``，需内核）保证。两者边界不可混淆。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from odin_sim.analysis import aggregate, aggregate_timeseries, load_run_series
from odin_sim.ui import background, plotly_utils
from odin_sim.ui.config_bridge import validate_sweep_config
from odin_sim.ui.pages import configure as configure_page
from odin_sim.ui.pages import runs as runs_page

MODEL = "reference-v1"
# 标量指标（扁平化列名 = 指标名.子键；backfire 需配对对照，此处不参与通用聚合）
SCALAR_METRIC = "ref.extreme_final_ord.extreme_final_ord"
SERIES_COLUMN = "ord_extreme"


def _config() -> dict:
    """UI 默认配置的最小改造：固定参考模型 + 开启序列落盘。

    走 ``build_default_config`` 而非手写，确保测的是 UI 真实产出的配置形状。
    """
    cfg = configure_page.build_default_config()
    cfg["name"] = "ref-loop"
    cfg["model"] = MODEL
    cfg["store_trajectory"] = True  # 让演化曲线页有序列可读
    cfg["axes"] = {"beta_gov": [0.0, 0.4]}
    cfg["seeds"] = {"start": 1, "count": 2, "step": 1}
    return cfg


@pytest.fixture(scope="module")
def swept(tmp_path_factory) -> Path:
    """跑一次小网格（4 条运行），供后续各页读取——不重跑。"""
    root = tmp_path_factory.mktemp("ref_loop")
    validation = validate_sweep_config(_config())
    assert validation.ok, validation.error
    summary = configure_page.trigger_sweep(validation.spec, root, metrics=["all"])
    assert summary["ok"] is True, summary
    assert summary["failed"] == 0
    assert summary["total"] == 4
    return Path(root)


def test_sweep_artifacts_schema(swept):
    """运行路径产出的产物 schema 完整（结果表 + 逐 run manifest + 序列）。"""
    df = runs_page.load_results_df(swept)
    assert df is not None and len(df) == 4
    for col in ("run_id", "config_id", "model", "beta_gov", "seed", SCALAR_METRIC):
        assert col in df.columns
    assert set(df["model"]) == {MODEL}
    # 比例量必须落在 [0,1]，否则分析层会被脏数据污染
    assert df[SCALAR_METRIC].between(0, 1).all()

    manifests = runs_page.iter_run_manifests(swept)
    assert len(manifests) == 4


def test_browse_page_reads_artifacts(swept):
    """浏览页只读消费：sweep manifest 概览 + 逐 run 明细均可载入。"""
    manifest = runs_page.load_sweep_manifest(swept)
    assert manifest is not None
    assert manifest["name"] == "ref-loop"
    summary = manifest.get("counts") or manifest.get("summary") or {}
    assert summary == {} or summary.get("total") == 4 or summary.get("executed") == 4


def test_explore_page_aggregation_and_figure(swept):
    """可视化页：analysis.aggregate 聚合 + plotly 成图 + HTML 导出。"""
    records = runs_page.load_results_df(swept).to_dict("records")
    rows = aggregate(records, ["beta_gov"], [SCALAR_METRIC])
    assert rows
    # results.csv 往返后分组键是字符串，按数值比较
    assert sorted({float(r["beta_gov"]) for r in rows}) == [0.0, 0.4]
    # 每条分组都有均值与置信区间
    assert all(f"{SCALAR_METRIC}.mean" in r for r in rows)
    assert all(f"{SCALAR_METRIC}.ci_low" in r for r in rows)

    fig = plotly_utils.metric_figure(rows, "beta_gov", [SCALAR_METRIC])
    assert len(fig.data) >= 1
    html = plotly_utils.figure_to_html(fig)
    assert "<div" in html and "plotly" in html.lower()


def test_timeseries_page_aggregation_and_figure(swept):
    """演化曲线页：逐时间步序列聚合（均值 + 置信带）后成图。"""
    records = load_run_series(swept, role="main")
    assert records, "参考模型应落盘 timeseries.csv"

    series_cols = [
        c for c in records[0]
        if c not in ("run_id", "step") and isinstance(records[0][c], np.ndarray)
    ]
    assert series_cols, "未发现序列列"
    column = SERIES_COLUMN if SERIES_COLUMN in series_cols else series_cols[0]

    rows = aggregate_timeseries(records, ["beta_gov"], [column])
    assert rows
    for r in rows:
        mean = r[f"{column}.mean"]
        assert np.asarray(mean).size > 1  # 是完整序列，不是末点标量
        assert np.asarray(r[f"{column}.ci_low"]).size == np.asarray(mean).size

    fig = plotly_utils.timeseries_figure(rows, [column], ["beta_gov"])
    assert len(fig.data) >= 1


def test_background_subprocess_real_run(tmp_path):
    """P2-3 后台路径用**真实子进程**验证（此前只能用 fake 进程桩）。

    参考模型让这条路径第一次能在无内核环境端到端跑：起 CLI 子进程 →
    轮询 ``runs/<id>/manifest.json`` 计数 → 完成后产物与同步路径同 schema。
    """
    root = tmp_path / "bg"
    cfg = _config()
    cfg["name"] = "ref-bg"

    proc = background.launch(cfg, root, metrics=["all"])

    status = None
    deadline = time.time() + 180
    while time.time() < deadline:
        status = background.task_status(proc, root, total=4)
        if status["state"] != "running":
            break
        time.sleep(0.3)

    assert status is not None
    assert status["state"] == "done", background.read_log_tail(root)
    assert status["done"] == 4
    assert (root / "sweep_config.json").is_file()  # 配置已落盘，供复现

    df = runs_page.load_results_df(root)
    assert df is not None and len(df) == 4
    assert SCALAR_METRIC in df.columns


def test_rerun_is_idempotent(swept):
    """同配置重跑到同一目录：全部命中续跑幂等，不重复计算。"""
    validation = validate_sweep_config(_config())
    summary = configure_page.trigger_sweep(validation.spec, swept, metrics=["all"])
    assert summary["ok"] is True
    assert summary["executed"] == 0
    assert summary["skipped"] == summary["total"] == 4
    # 幂等不应改变已有结果行数
    assert len(runs_page.load_results_df(swept)) == 4
