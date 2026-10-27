"""G3 适配器 + 引擎链路的回归测试。

与 ``test_g3_fingerprint.py`` 的区别
----------------------------------
指纹测试直接调用 baseline 的 ``simulate``；本测试走**平台自己的链路**：
注册表 → 模型适配器 → 引擎 → 产物。若这条链路改变了任何一个数值，
``traj_hash`` 就会与数据集不一致，测试即失败。
"""
from __future__ import annotations

import numpy as np
import pytest

from odin_sim.adapters.baseline_g3 import BaselineError, load_kernel, resolve_root
from odin_sim.engine import run_once
from odin_sim.model import DYNAMICS, TOPOLOGY
from odin_sim.model.protocol import NetworkSpec, RunRequest
from odin_sim.verification import read_rows


def _kernel():
    try:
        root = resolve_root()
    except BaselineError as exc:
        pytest.skip(f"未配置 baseline：{exc}")
    try:
        return load_kernel(root)
    except BaselineError as exc:
        pytest.skip(f"baseline 不可用：{exc}")


def _rows(kernel, name: str = "g3_grid_runs.csv", limit: int = 5):
    data = kernel.root / "data" / name
    if not data.is_file():
        pytest.skip(f"缺少数据集：{data}")
    return read_rows(data)[:limit]


def test_engine_driven_run_matches_baseline_fingerprint():
    kernel = _kernel()
    if not DYNAMICS.has("g3-baseline-v1"):
        pytest.skip("G3 适配器未注册（baseline 不可用）")

    model = DYNAMICS.get("g3-baseline-v1")
    rows = _rows(kernel)

    for row in rows:
        req = RunRequest(
            mode=row["mode"],
            placement=row["placement"],
            beta_gov=float(row["beta_gov"]),
            tau=int(row["tau"]),
            seed=int(row["seed"]),
            scenario="polarized",
        )
        rec = run_once(model, req)
        assert rec.traj_hash == row["traj_hash"], (
            f"引擎链路改变了轨迹：{req.config_id} seed={req.seed}"
        )
        assert rec.network_hash == row["network_hash"], (
            f"引擎链路改变了网络：{req.config_id} seed={req.seed}"
        )


def test_run_output_series_are_consistent_with_row():
    kernel = _kernel()
    if not DYNAMICS.has("g3-baseline-v1"):
        pytest.skip("G3 适配器未注册（baseline 不可用）")

    model = DYNAMICS.get("g3-baseline-v1")
    first = _rows(kernel, limit=1)[0]
    req = RunRequest(
        mode=first["mode"],
        placement=first["placement"],
        beta_gov=float(first["beta_gov"]),
        tau=int(first["tau"]),
        seed=int(first["seed"]),
    )
    out = run_once(model, req).output

    # series_ord 的末值必须与数据集记录的 extreme_final_ord 一致
    assert float(out.series_ord["extreme"][-1]) == pytest.approx(
        float(first["extreme_final_ord"])
    )
    assert int(out.cost["n_adv"]) == int(first["n_adv"])
    assert int(out.cost["n_gov"]) == int(first["n_gov"])


def test_topology_adapter_matches_baseline_network():
    kernel = _kernel()
    if not TOPOLOGY.has("g3-baseline-v1"):
        pytest.skip("G3 拓扑适配器未注册（baseline 不可用）")

    topo = TOPOLOGY.get("g3-baseline-v1")
    spec = NetworkSpec(kind="ba", n=200, seed=42)
    net = topo.build(spec)

    direct = kernel.build_network(200, 42, "ba", 3, signs=None, homophily=0.0)
    assert net.hash == direct["hash"]
    assert net.n_edges == direct["n_edges"]
    assert np.array_equal(net.deg, direct["deg"])


def test_topology_adapter_rejects_unsupported_kind():
    kernel = _kernel()
    if not TOPOLOGY.has("g3-baseline-v1"):
        pytest.skip("G3 拓扑适配器未注册（baseline 不可用）")

    topo = TOPOLOGY.get("g3-baseline-v1")
    with pytest.raises(ValueError, match="不支持的拓扑"):
        topo.build(NetworkSpec(kind="sbm", n=100, seed=1))
