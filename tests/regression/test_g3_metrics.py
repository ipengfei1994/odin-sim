"""G3 指标等价性回归。

验证两件事
----------
1. 平台重算的**整行指标**与 baseline 数据集逐字段一致（口径回归）。
2. 通过 ``Metric`` 协议单独调用指标，结果与整行口径一致（协议回归）。
"""
from __future__ import annotations

import numpy as np
import pytest

from odin_sim.adapters.baseline_g3 import BaselineError, load_kernel, resolve_root
from odin_sim.model import DYNAMICS, METRICS
from odin_sim.model.protocol import MetricContext, RunRequest
from odin_sim.verification import read_rows, verify_metric_rows


def _kernel():
    try:
        root = resolve_root()
    except BaselineError as exc:
        pytest.skip(f"未配置 baseline：{exc}")
    try:
        return load_kernel(root)
    except BaselineError as exc:
        pytest.skip(f"baseline 不可用：{exc}")


def test_metric_rows_match_baseline_dataset():
    kernel = _kernel()
    data = kernel.root / "data" / "g3_grid_runs.csv"
    if not data.is_file():
        pytest.skip(f"缺少数据集：{data}")

    rows = read_rows(data)[:6]
    result = verify_metric_rows(kernel, rows, scenario="polarized")

    detail = "; ".join(
        f"row={m.row_index} {m.config_id} seed={m.seed} {m.field_name}: "
        f"{m.expected} != {m.actual}"
        for m in result.mismatches
    )
    assert result.ok, f"指标不一致：{detail}"


def test_protocol_metrics_agree_with_row_builder():
    """用 Metric 协议单独算，结果必须与数据集同一口径。"""
    kernel = _kernel()
    if not DYNAMICS.has("g3-baseline-v1") or not METRICS.has("g3.extreme_final_ord"):
        pytest.skip("G3 适配器未注册（baseline 不可用）")

    data = kernel.root / "data" / "g3_grid_runs.csv"
    if not data.is_file():
        pytest.skip(f"缺少数据集：{data}")
    row = read_rows(data)[0]

    model = DYNAMICS.get("g3-baseline-v1")
    req = RunRequest(
        mode=row["mode"],
        placement=row["placement"],
        beta_gov=float(row["beta_gov"]),
        tau=int(row["tau"]),
        seed=int(row["seed"]),
    )
    out = model.run(req)
    params = kernel.scenario_params("polarized").to_dict()

    ctx = MetricContext(
        traj=out.traj,
        is_adv=out.is_adv,
        is_gov=out.is_gov,
        params=params,
    )

    ext = METRICS.get("g3.extreme_final_ord").compute(ctx)
    assert ext["extreme_final_ord"] == pytest.approx(float(row["extreme_final_ord"]))
    assert ext["extreme_t0_ord"] == pytest.approx(float(row["extreme_t0_ord"]))

    joint = METRICS.get("g3.joint_final_ord").compute(ctx)
    assert joint["joint_final_ord"] == pytest.approx(float(row["joint_final_ord"]))

    alert = METRICS.get("g3.alert_lead_time").compute(ctx)
    assert alert["censored"] == (row["censored"] == "True")
    if alert["alert_step"] is None:
        assert row["alert_step"] == ""
    else:
        assert alert["alert_step"] == int(row["alert_step"])


def test_backfire_metric_refuses_missing_control():
    kernel = _kernel()
    if not METRICS.has("g3.backfire"):
        pytest.skip("G3 指标未注册（baseline 不可用）")

    ctx = MetricContext(
        traj=np.zeros((2, 3, 2)),
        is_adv=np.zeros(3, dtype=bool),
        is_gov=np.zeros(3, dtype=bool),
        params=kernel.scenario_params("polarized").to_dict(),
    )
    with pytest.raises(ValueError, match="control_traj"):
        METRICS.get("g3.backfire").compute(ctx)
