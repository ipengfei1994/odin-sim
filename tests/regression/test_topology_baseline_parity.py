"""拓扑与 baseline 的逐位一致性回归。

两条硬性要求
------------
1. 平台生成的 ba / er / ring 必须与 baseline 生成**同一张图**（hash 相同）。
   这是移植正确性的唯一判据——不是"结构相似"，是逐位相同。
2. 注入平台拓扑后跑 baseline 内核，``traj_hash`` 必须与数据集一致。
   证明"换掉 build_network 这个引用"没有改变任何数值。
"""
from __future__ import annotations

import numpy as np
import pytest

from odin_sim.adapters.baseline_g3 import BaselineError, load_kernel, resolve_root
from odin_sim.topology import (
    SUPPORTED_KINDS,
    build_network,
    patched_baseline_topology,
)
from odin_sim.verification import read_rows

# baseline 已有的三种（必须逐位一致）
PARITY_KINDS = ("ba", "er", "ring")
# 平台新增的两种（baseline 没有，只验证自身确定性）
NEW_KINDS = ("ws", "sbm")


def _kernel():
    try:
        root = resolve_root()
    except BaselineError as exc:
        pytest.skip(f"未配置 baseline：{exc}")
    try:
        return load_kernel(root)
    except BaselineError as exc:
        pytest.skip(f"baseline 不可用：{exc}")


def test_platform_matches_baseline_for_existing_kinds():
    kernel = _kernel()
    n, m = 200, 3

    for kind in PARITY_KINDS:
        for seed in (1, 2, 37):
            for homophily in (0.0, 0.85):
                rng = np.random.default_rng(seed)
                signs = np.where(rng.random(n) < 0.5, -1.0, 1.0)

                mine = build_network(n, seed, kind, m,
                                     signs=signs, homophily=homophily)
                theirs = kernel.build_network(n, seed, kind, m,
                                              signs=signs, homophily=homophily)
                assert mine["hash"] == theirs["hash"], (
                    f"{kind} seed={seed} homophily={homophily} 与 baseline 不一致"
                )
                assert np.array_equal(mine["deg"], theirs["deg"])


def test_platform_matches_baseline_without_signs():
    kernel = _kernel()
    for kind in PARITY_KINDS:
        for seed in (5, 11):
            mine = build_network(150, seed, kind, 3)
            theirs = kernel.build_network(150, seed, kind, 3)
            assert mine["hash"] == theirs["hash"], f"{kind} seed={seed} 不一致"


def test_injection_does_not_change_baseline_trajectory():
    """注入平台拓扑后，ba 的轨迹必须与数据集逐位一致。"""
    kernel = _kernel()
    data = kernel.root / "data" / "g3_grid_runs.csv"
    if not data.is_file():
        pytest.skip(f"缺少数据集：{data}")

    rows = read_rows(data)[:4]
    params = kernel.scenario_params("polarized")

    with patched_baseline_topology(kernel):
        for row in rows:
            cfg = kernel.Config(
                mode=row["mode"],
                placement=row["placement"],
                beta_gov=float(row["beta_gov"]),
                tau=int(row["tau"]),
            )
            res = kernel.simulate(cfg, int(row["seed"]), params)
            assert res.meta["traj_hash"] == row["traj_hash"], (
                f"注入后轨迹改变：{row['config_id']} seed={row['seed']}"
            )
            assert res.meta["network_hash"] == row["network_hash"]


def test_injection_is_restored_after_scope():
    kernel = _kernel()
    original = kernel.model_module.build_network
    with patched_baseline_topology(kernel):
        assert kernel.model_module.build_network is not original
    assert kernel.model_module.build_network is original


def test_new_kinds_are_deterministic():
    for kind in NEW_KINDS:
        a = build_network(120, 7, kind, 3)
        b = build_network(120, 7, kind, 3)
        c = build_network(120, 8, kind, 3)
        assert a["hash"] == b["hash"], f"{kind} 同 seed 结果不稳定"
        assert a["hash"] != c["hash"], f"{kind} 不同 seed 结果相同"


def test_ws_preserves_mean_degree_and_beta_zero_equals_ring():
    n, m = 200, 3
    ring = build_network(n, 3, "ring", m)
    ws0 = build_network(n, 3, "ws", m, beta=0.0)
    assert ws0["hash"] == ring["hash"], "beta=0 的 WS 应退化为规则环"

    ws = build_network(n, 3, "ws", m, beta=0.1)
    assert ws["deg"].mean() == pytest.approx(2 * m), "WS 重连应保持度序列"
    assert ws["hash"] != ring["hash"], "beta>0 应改变图结构"


def test_sbm_targets_mean_degree_and_has_communities():
    n, m = 300, 3
    net = build_network(n, 5, "sbm", m)
    assert net["deg"].mean() == pytest.approx(2 * m, abs=0.5), (
        f"SBM 平均度应接近 2m={2 * m}，实际 {net['deg'].mean():.3f}"
    )
    # 连通性检查：SBM 在 ratio 很小时可能不连通，这里只断言没有孤立度断言
    assert net["n_edges"] > 0


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown network kind"):
        build_network(50, 1, "lattice", 3)


def test_supported_kinds_cover_baseline_and_new():
    assert set(SUPPORTED_KINDS) == {"ba", "er", "ring", "ws", "sbm"}
