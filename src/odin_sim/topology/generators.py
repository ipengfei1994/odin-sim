"""网络生成器（平台侧原生实现，纯 numpy，不依赖 networkx）。

代码来源与一致性
----------------
``ba`` / ``er`` / ``ring`` 三种拓扑的**随机数消耗方式**必须与外部参考实现
完全一致，否则同一 seed 会得到不同的图、``traj_hash`` 全军覆没。这三种
是照参考实现的拓扑模块移植的，并由
``tests/regression/test_topology_baseline_parity.py`` 用 ``network_hash``
逐位验证——不是"应当一致"，而是每次测试都会验证。

``ws`` (Watts–Strogatz) 与 ``sbm`` (随机块模型) 是**本平台新增**，参考实现
没有对应实现。它们的随机数消耗方式由本模块定义，属于新增研究假设，
已登记在 ``docs/topology-assumptions.md``（A-1 / A-2）。

为什么不用 networkx
------------------
与 baseline 同理由：BA 生成的随机数消耗方式在不同 networkx 版本间有过变化，
跨机器复现会把整条依赖链钉死。这里用 numpy 的 PCG64 自行实现，只需锁定
numpy 主版本。
"""
from __future__ import annotations

import hashlib

import numpy as np

__all__ = [
    "SUPPORTED_KINDS",
    "network_hash",
    "build_network",
]

SUPPORTED_KINDS = ("ba", "er", "ring", "ws", "sbm")

# 新增拓扑的默认参数（研究假设 A-1 / A-2，见 docs/topology-assumptions.md）
DEFAULT_WS_BETA = 0.10
DEFAULT_SBM_BLOCKS = 4
DEFAULT_SBM_RATIO = 0.20


def _empty_adj(n: int) -> list[set[int]]:
    return [set() for _ in range(n)]


# ---------------------------------------------------------------- 基础三种


def _build_ba(n: int, rng: np.random.Generator, m: int) -> list[set[int]]:
    """Barabási–Albert 无标度网络（与 baseline 逐位一致）。"""
    m0 = m + 1
    adj = _empty_adj(n)
    for i in range(m0):
        for j in range(i + 1, m0):
            adj[i].add(j)
            adj[j].add(i)
    repeated: list[int] = []
    for node in range(m0):
        repeated.extend([node] * len(adj[node]))
    for node in range(m0, n):
        chosen: set[int] = set()
        while len(chosen) < m:
            cand = repeated[int(rng.integers(0, len(repeated)))]
            if cand != node:
                chosen.add(cand)
        for tgt in sorted(chosen):
            adj[node].add(tgt)
            adj[tgt].add(node)
        repeated.extend(sorted(chosen))
        repeated.extend([node] * m)
    return adj


def _build_er(n: int, rng: np.random.Generator, m: int) -> list[set[int]]:
    """Erdős–Rényi 随机网络，平均度 2m（与 baseline 逐位一致）。"""
    p = 2.0 * m / max(n - 1, 1)
    adj = _empty_adj(n)
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < p:
                adj[i].add(j)
                adj[j].add(i)
    return adj


def _build_ring(n: int, rng: np.random.Generator, m: int) -> list[set[int]]:
    """规则环（每节点连左右各 m 个近邻，度 2m）。环不消耗随机数。"""
    adj = _empty_adj(n)
    for i in range(n):
        for k in range(1, m + 1):
            j = (i + k) % n
            adj[i].add(j)
            adj[j].add(i)
    return adj


# ---------------------------------------------------------------- 新增两种


def _build_ws(n: int, rng: np.random.Generator, m: int,
              beta: float = DEFAULT_WS_BETA) -> list[set[int]]:
    """Watts–Strogatz 小世界网络（本平台新增，假设 A-1）。

    做法：先建规则环（同 ``ring``），再按 (i, k) 升序遍历每条边，
    以概率 ``beta`` 把 (i,j) 重连为 (i,k')。

    约定（都会影响随机数序列，故显式写死）：
    * 遍历顺序：i 升序，k = 1..m 升序，边记作 (i, (i+k) % n)；
    * 每条边先抽一次 ``rng.random()`` 决定是否重连；
    * 重连目标用 ``rng.integers(0, n)`` 拒绝采样，最多 20 次；
      20 次内没找到合法目标则**放弃本次重连，保留原边**；
    * 禁止自环与重边。
    * 度数守恒：重连只换 j，不增删边，故平均度仍为 2m。
    """
    adj = _build_ring(n, rng, m)
    if beta <= 0.0:
        return adj
    for i in range(n):
        for k in range(1, m + 1):
            j = (i + k) % n
            if rng.random() >= beta:
                continue
            adj[i].discard(j)
            adj[j].discard(i)
            target = None
            for _ in range(20):
                cand = int(rng.integers(0, n))
                if cand != i and cand not in adj[i]:
                    target = cand
                    break
            if target is None:
                adj[i].add(j)
                adj[j].add(i)
            else:
                adj[i].add(target)
                adj[target].add(i)
    return adj


def _block_sizes(n: int, n_blocks: int) -> list[int]:
    """等大小划分（余数分给前几个块），保证确定性且与立场无关。"""
    base, rem = divmod(n, n_blocks)
    return [base + (1 if b < rem else 0) for b in range(n_blocks)]


def _build_sbm(n: int, rng: np.random.Generator, m: int,
               n_blocks: int = DEFAULT_SBM_BLOCKS,
               ratio: float = DEFAULT_SBM_RATIO) -> list[set[int]]:
    """随机块模型（本平台新增，假设 A-2）。

    参数
    ----
    n_blocks : 社区数，节点按 id **连续**划分（block = 所属区间），
               与初始立场无关——立场相关的社群结构由 ``homophily`` 重连
               单独建模，两者不混在一个机制里。
    ratio    : p_out / p_in，跨块边相对块内边的稀疏程度。
    p_in     : 不是自由参数，由目标平均度 2m 反解：
               mean_deg = (size-1)*p_in + (n-size)*p_out,  p_out = ratio*p_in

    随机数消耗：按 (i, j) 升序遍历全部节点对，每对抽一次 ``rng.random()``。
    """
    sizes = _block_sizes(n, n_blocks)
    block_id = np.empty(n, dtype=np.int32)
    start = 0
    for b, size in enumerate(sizes):
        block_id[start:start + size] = b
        start += size

    size = sizes[0]
    denom = (size - 1) + (n - size) * ratio
    p_in = float(np.clip(2.0 * m / denom, 0.0, 1.0)) if denom > 0 else 0.0
    p_out = float(np.clip(p_in * ratio, 0.0, 1.0))

    adj = _empty_adj(n)
    for i in range(n):
        bi = block_id[i]
        for j in range(i + 1, n):
            p = p_in if block_id[j] == bi else p_out
            if rng.random() < p:
                adj[i].add(j)
                adj[j].add(i)
    return adj


# ---------------------------------------------------------------- 同配重连


def _cross_ratio(adj: list[set[int]], signs: np.ndarray | None) -> float:
    if signs is None:
        return float("nan")
    tot = cross = 0
    for i in range(len(adj)):
        for j in adj[i]:
            if j > i:
                tot += 1
                if signs[i] != signs[j]:
                    cross += 1
    return float(cross / tot) if tot else float("nan")


def _rewire_homophily(adj: list[set[int]], signs: np.ndarray,
                      homophily: float, rng) -> tuple[list[set[int]], float]:
    """按概率把跨立场边重连为同立场边（与 baseline 同一算法）。

    这是对"回声室"的最小建模：不引入新的动力学参数，只在网络层抑制
    跨立场接触。度序列大致保持不变。
    """
    by_sign = {1.0: np.flatnonzero(signs > 0), -1.0: np.flatnonzero(signs <= 0)}
    for i in range(len(adj)):
        for j in list(adj[i]):
            if j <= i:
                continue
            if signs[i] == signs[j]:
                continue
            if rng.random() >= homophily:
                continue
            pool = by_sign[1.0 if signs[i] > 0 else -1.0]
            for _ in range(20):
                k = int(pool[int(rng.integers(0, len(pool)))])
                if k == i or k in adj[i]:
                    continue
                adj[i].discard(j)
                adj[j].discard(i)
                adj[i].add(k)
                adj[k].add(i)
                break
    return adj, _cross_ratio(adj, signs)


def network_hash(net: dict) -> str:
    """图结构指纹（与 baseline 同一算法：sha1(src+dst)[:16]）。"""
    h = hashlib.sha1()
    h.update(net["src"].tobytes())
    h.update(net["dst"].tobytes())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- 对外入口


def build_network(n: int, seed: int, kind: str = "ba", m: int = 3,
                  signs: np.ndarray | None = None, homophily: float = 0.0,
                  **options) -> dict:
    """生成无向网络，返回 COO 形式的双向边表。

    与 baseline ``topology.build_network`` 的签名与返回结构一致，因此可以
    直接注入替换（见 ``odin_sim.topology.inject``）。

    返回 dict(src, dst, deg, n_edges, hash, cross_edge_ratio)
    """
    if kind not in SUPPORTED_KINDS:
        raise ValueError(
            f"unknown network kind: {kind}；支持：{SUPPORTED_KINDS}"
        )
    rng = np.random.default_rng(np.random.SeedSequence([seed, 1101]))

    if kind == "ba":
        adj = _build_ba(n, rng, m)
    elif kind == "er":
        adj = _build_er(n, rng, m)
    elif kind == "ring":
        adj = _build_ring(n, rng, m)
    elif kind == "ws":
        adj = _build_ws(n, rng, m, float(options.get("beta", DEFAULT_WS_BETA)))
    else:  # sbm
        adj = _build_sbm(
            n, rng, m,
            int(options.get("n_blocks", DEFAULT_SBM_BLOCKS)),
            float(options.get("ratio", DEFAULT_SBM_RATIO)),
        )

    if signs is not None and homophily > 0.0:
        adj, cross_ratio = _rewire_homophily(adj, np.asarray(signs), homophily, rng)
    else:
        cross_ratio = _cross_ratio(adj, np.asarray(signs) if signs is not None else None)

    src: list[int] = []
    dst: list[int] = []
    for i in range(n):
        for j in sorted(adj[i]):
            src.append(i)
            dst.append(j)
    src_a = np.asarray(src, dtype=np.int32)
    dst_a = np.asarray(dst, dtype=np.int32)
    deg = np.bincount(src_a, minlength=n).astype(np.int32)
    net = {
        "src": src_a,
        "dst": dst_a,
        "deg": deg,
        "n_edges": int(len(src_a) // 2),
        "cross_edge_ratio": cross_ratio,
        "kind": kind,
    }
    net["hash"] = network_hash(net)
    return net
