"""G3 baseline 指纹回归测试。

目的：证明平台侧复算的轨迹与 baseline 数据集逐位一致。
需要能定位到 baseline：设置环境变量 ODIN_BASELINE_ROOT，否则跳过。
"""
from __future__ import annotations

import pytest

from odin_sim.adapters.baseline_g3 import BaselineError, load_kernel, resolve_root
from odin_sim.verification import read_rows, verify_rows


def _kernel():
    try:
        root = resolve_root()
    except BaselineError as exc:
        pytest.skip(f"未配置 baseline：{exc}")
    try:
        return load_kernel(root)
    except BaselineError as exc:
        pytest.skip(f"baseline 不可用：{exc}")


def test_traj_hash_matches_baseline_dataset():
    kernel = _kernel()
    data = kernel.root / "data" / "g3_grid_runs.csv"
    if not data.is_file():
        pytest.skip(f"缺少数据集：{data}")

    rows = read_rows(data)[:5]
    result = verify_rows(kernel, rows, scenario="polarized")

    detail = "; ".join(
        f"row={m.row_index} {m.config_id} seed={m.seed} {m.field_name}: "
        f"{m.expected} != {m.actual}"
        for m in result.mismatches
    )
    assert result.ok, f"指纹不一致：{detail}"


def test_kernel_model_version_is_expected():
    kernel = _kernel()
    assert kernel.model_version == "g3-baseline-v1.0.0"
