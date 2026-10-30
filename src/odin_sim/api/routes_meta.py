"""元信息类端点：环境、模型、指标、拓扑。

设计约束：**所有清单都来自注册表实读**，不在服务层写死任何模型或拓扑名字。
注册表里没有的东西，界面上就必须看不见——避免"声称支持某个模型但实际跑不了"
这类虚假能力承诺。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import __version__
from ..model import DYNAMICS, METRICS, TOPOLOGY, builtin_registration_error
from ..model.protocol import NetworkSpec
from ..reproducibility import snapshot
from ..topology.generators import SUPPORTED_KINDS
from ..topology.model import PlatformTopology
from .deps import resolve_results_root

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/info")
def api_info() -> dict:
    """运行环境快照：Python 版本、提交号、脏标记、依赖版本。

    刻意实时采集而非写死常数：写死的那一刻它就是假的，而 reproducibility
    的全部价值在于"环境变了也能对得上"。
    """
    return snapshot(packages=("numpy",), extra={"odin_sim_version": __version__})


@router.get("/models")
def api_models() -> dict:
    """可用动力学模型清单（源自 ``DYNAMICS`` 注册表）。"""
    error = builtin_registration_error()
    models = [
        {
            "name": name,
            "external_kernel": name.startswith("g3"),
        }
        for name in DYNAMICS.names()
    ]
    return {"models": models, "registration_error": error}


@router.get("/metrics")
def api_metrics() -> dict:
    """已注册指标清单，含是否需要配对对照（决定该指标能否被 URI 计算）。"""
    items = []
    for name in METRICS.names():
        metric = METRICS.get(name)
        items.append(
            {
                "name": name,
                "requires_control": bool(getattr(metric, "requires_control", False)),
            }
        )
    return {"metrics": items}


@router.get("/topologies")
def api_topologies() -> dict:
    return {"topologies": TOPOLOGY.names()}


@router.get("/network")
def api_network(
    kind: str = "ba",
    n: int = 200,
    seed: int = 1,
    m: int = 3,
    beta: float | None = None,
    homophily: float = 0.0,
    n_blocks: int | None = None,
    ratio: float | None = None,
) -> dict:
    """按需确定性生成拓扑并给出布局坐标，供 Network explorer 可视化。

    这不是"读取某个已存产物"，而是用平台原生拓扑生成器按给定参数当场生成——
    同一组参数永远得到同一张图（hash 一致），契合可复现原则。离线、无副作用。
    """
    if kind not in SUPPORTED_KINDS:
        raise HTTPException(status_code=422, detail=f"不支持的拓扑：{kind}；支持 {list(SUPPORTED_KINDS)}")
    if n < 2 or n > 5000:
        raise HTTPException(status_code=422, detail="n 必须在 [2, 5000] 之间")
    if m < 1:
        raise HTTPException(status_code=422, detail="m 必须 >= 1")

    options: dict[str, Any] = {"m": m, "homophily": homophily}
    if beta is not None:
        options["beta"] = beta
    if n_blocks is not None:
        options["n_blocks"] = n_blocks
    if ratio is not None:
        options["ratio"] = ratio

    topo = PlatformTopology()
    net = topo.build(NetworkSpec(kind=kind, n=n, seed=seed, options=options))

    import networkx as nx

    g = nx.Graph()
    edges = [(int(a), int(b)) for a, b in zip(net.src, net.dst)]
    g.add_nodes_from(range(n))
    g.add_edges_from(edges)
    pos = nx.spring_layout(g, seed=seed, weight=None)
    nodes = [
        {"id": i, "x": float(pos[i][0]), "y": float(pos[i][1]), "deg": int(net.deg[i])}
        for i in range(n)
    ]
    return {
        "kind": kind,
        "n": n,
        "n_edges": int(net.n_edges),
        "hash": net.hash,
        "extra": net.extra,
        "nodes": nodes,
        "edges": edges,
    }
