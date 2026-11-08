"""S3｜四张图的持久化与基础图运算。

三网络分离（S-3 / Q-24）
------------------------
``potential`` 谁能接触到谁（潜在关系，来自拓扑）
``exposure``  谁实际看到了谁的信息（曝光关系，来自分发 + 选择）
``influence`` 谁的意见真的改变了谁（影响关系）
``interaction`` 互动图（3C：评论/点赞/转发构成的有向图）

影响网络边**不得**仅凭观点相关或时间先后自动建立：
:meth:`NetworkGraph.add_influence` 要求显式 ``relation_kind`` 与证据引用，
``inferred`` 还必须给出推断方法与置信度。本模块不提供任何"自动推断因果"
的便利函数——那正是规范禁止的做法。

本模块的图运算是**原始观测量**（edge_count / density / degree / 连通性等），
不冠以 v2 指标名；v2 的模块度 Q、同配系数等必须由指标层在定义锁定后才计算。
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .records import NETWORK_TYPES, RELATION_KINDS, NetworkSnapshot

__all__ = [
    "NETWORK_FILES", "NetworkError", "Edge", "NetworkGraph", "NetworkStore",
    "edge_count", "density", "degree_sequence", "cross_edge_ratio",
    "jaccard_overlap", "avg_shortest_path_length", "connected_components",
]

NETWORK_FILES = {kind: f"network_{kind}.json" for kind in NETWORK_TYPES}


class NetworkError(ValueError):
    """网络构建或持久化失败。"""


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    weight: float = 1.0
    created_tick: int = 0
    removed_tick: int | None = None
    relation_kind: str = ""
    evidence_refs: tuple[str, ...] = ()
    inferred_method: str = ""
    confidence: float | None = None


class NetworkGraph:
    """一张随时间演化的图。边的生命周期由 ``created_tick`` / ``removed_tick`` 记录。"""

    def __init__(
        self,
        network_type: str,
        *,
        directed: bool = True,
        weighted: bool = False,
        node_ids: Iterable[str] = (),
    ) -> None:
        if network_type not in NETWORK_TYPES:
            raise NetworkError(f"未知网络类型：{network_type}；允许 {list(NETWORK_TYPES)}")
        self.network_type = network_type
        self.directed = bool(directed)
        self.weighted = bool(weighted)
        self.nodes: set[str] = set(str(n) for n in node_ids)
        self.edges: dict[tuple[str, str], Edge] = {}

    @staticmethod
    def _key(src: str, dst: str, directed: bool) -> tuple[str, str]:
        return (src, dst) if directed else tuple(sorted((src, dst)))

    def add_node(self, node_id: str) -> None:
        self.nodes.add(str(node_id))

    def add_edge(
        self,
        src: str,
        dst: str,
        *,
        tick: int = 0,
        weight: float = 1.0,
        relation_kind: str = "",
        evidence_refs: Sequence[str] = (),
        inferred_method: str = "",
        confidence: float | None = None,
    ) -> Edge:
        src, dst = str(src), str(dst)
        self.nodes.update((src, dst))
        key = self._key(src, dst, self.directed)
        edge = Edge(
            src=src, dst=dst, weight=float(weight), created_tick=int(tick),
            relation_kind=relation_kind, evidence_refs=tuple(evidence_refs),
            inferred_method=inferred_method, confidence=confidence,
        )
        self.edges[key] = edge
        return edge

    def add_influence(
        self,
        src: str,
        dst: str,
        *,
        tick: int = 0,
        relation_kind: str,
        evidence_refs: Sequence[str],
        inferred_method: str = "",
        confidence: float | None = None,
        weight: float = 1.0,
    ) -> Edge:
        """影响网络专用入口：强制证据与关系类型（Q-24）。"""
        if self.network_type != "influence":
            raise NetworkError("add_influence 只能用于 influence 网络")
        if relation_kind not in RELATION_KINDS:
            raise NetworkError(f"relation_kind 非法：{relation_kind}；允许 {list(RELATION_KINDS)}")
        if not evidence_refs:
            raise NetworkError("影响边必须携带证据引用；禁止仅凭观点相关或时序建边")
        if relation_kind == "inferred":
            if not inferred_method:
                raise NetworkError("inferred 影响边必须声明推断方法")
            if confidence is None:
                raise NetworkError("inferred 影响边必须给出置信度")
        return self.add_edge(
            src, dst, tick=tick, weight=weight, relation_kind=relation_kind,
            evidence_refs=evidence_refs, inferred_method=inferred_method,
            confidence=confidence,
        )

    def remove_edge(self, src: str, dst: str, *, tick: int, reason: str = "") -> Edge | None:
        key = self._key(str(src), str(dst), self.directed)
        edge = self.edges.get(key)
        if edge is None:
            return None
        from dataclasses import replace
        updated = replace(edge, removed_tick=int(tick))
        self.edges[key] = updated
        return updated

    def active_edges(self, tick: int | None = None) -> list[Edge]:
        """取在 ``tick`` 时仍存活的边；``tick=None`` 表示取当前全部。"""
        out = []
        for edge in self.edges.values():
            if tick is not None and edge.created_tick > tick:
                continue
            if tick is not None and edge.removed_tick is not None and edge.removed_tick <= tick:
                continue
            if edge.removed_tick is not None and tick is None:
                continue
            out.append(edge)
        return sorted(out, key=lambda e: (e.created_tick, e.src, e.dst))

    def snapshot(self, tick: int, snapshot_id: str = "", edges_ref: str = "") -> NetworkSnapshot:
        return NetworkSnapshot(
            snapshot_id=snapshot_id or f"{self.network_type}@{tick}",
            network_type=self.network_type,
            tick=int(tick),
            n_nodes=len(self.nodes),
            edges_ref=edges_ref or NETWORK_FILES[self.network_type],
            directed=self.directed,
            weighted=self.weighted,
            node_id_space="agent_id",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "network_type": self.network_type,
            "directed": self.directed,
            "weighted": self.weighted,
            "nodes": sorted(self.nodes),
            "edges": [asdict(e) for e in self.active_edges()],
            "all_edges": [asdict(e) for e in sorted(
                self.edges.values(), key=lambda e: (e.created_tick, e.src, e.dst)
            )],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NetworkGraph":
        graph = cls(
            str(data["network_type"]),
            directed=bool(data.get("directed", True)),
            weighted=bool(data.get("weighted", False)),
            node_ids=data.get("nodes") or (),
        )
        for raw in data.get("all_edges") or data.get("edges") or ():
            edge = Edge(
                src=str(raw["src"]), dst=str(raw["dst"]),
                weight=float(raw.get("weight", 1.0)),
                created_tick=int(raw.get("created_tick", 0)),
                removed_tick=raw.get("removed_tick"),
                relation_kind=str(raw.get("relation_kind", "")),
                evidence_refs=tuple(raw.get("evidence_refs") or ()),
                inferred_method=str(raw.get("inferred_method", "")),
                confidence=raw.get("confidence"),
            )
            graph.edges[graph._key(edge.src, edge.dst, graph.directed)] = edge
            graph.nodes.update((edge.src, edge.dst))
        return graph


class NetworkStore:
    """四张图的落盘与读取（S3：边表持久化，修复「只有 network_hash」缺口）。"""

    @staticmethod
    def save(graphs: Mapping[str, NetworkGraph], run_dir: str | Path) -> dict[str, str]:
        root = Path(run_dir)
        root.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}
        for kind, graph in graphs.items():
            if kind not in NETWORK_FILES:
                raise NetworkError(f"未知网络类型：{kind}")
            path = root / NETWORK_FILES[kind]
            path.write_text(
                json.dumps(graph.to_dict(), ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            written[kind] = path.name
        return written

    @staticmethod
    def load(run_dir: str | Path, kind: str) -> NetworkGraph | None:
        path = Path(run_dir) / NETWORK_FILES[kind]
        if not path.is_file():
            return None
        return NetworkGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def load_all(run_dir: str | Path) -> dict[str, NetworkGraph]:
        return {
            kind: graph
            for kind in NETWORK_TYPES
            if (graph := NetworkStore.load(run_dir, kind)) is not None
        }


# ---------------------------------------------------------------------------
# 原始观测量（无 v2 指标名，不做科学判定）
# ---------------------------------------------------------------------------
def _pairs(graph: NetworkGraph, tick: int | None = None) -> list[tuple[str, str]]:
    return [(e.src, e.dst) for e in graph.active_edges(tick)]


def edge_count(graph: NetworkGraph, tick: int | None = None) -> int:
    return len(_pairs(graph, tick))


def density(graph: NetworkGraph, tick: int | None = None) -> float:
    n = len(graph.nodes)
    if n < 2:
        return 0.0
    m = edge_count(graph, tick)
    return m / (n * (n - 1)) if graph.directed else 2.0 * m / (n * (n - 1))


def degree_sequence(graph: NetworkGraph, tick: int | None = None) -> dict[str, int]:
    out = {node: 0 for node in graph.nodes}
    for src, dst in _pairs(graph, tick):
        out[src] = out.get(src, 0) + 1
        if not graph.directed:
            out[dst] = out.get(dst, 0) + 1
    return out


def cross_edge_ratio(
    graph: NetworkGraph,
    partition: Mapping[str, int],
    tick: int | None = None,
) -> float:
    """跨社区边占比的**原始计数比**；社区划分口径由配置给定（Q-12）。"""
    edges = _pairs(graph, tick)
    if not edges:
        return float("nan")
    cross = sum(1 for s, d in edges if partition.get(s) != partition.get(d))
    return cross / len(edges)


def jaccard_overlap(a: NetworkGraph, b: NetworkGraph, tick: int | None = None) -> float:
    """两张图边集 Jaccard 重合度（Q-27 的候选度量之一，未锁定）。"""
    sa = {frozenset((s, d)) for s, d in _pairs(a, tick)}
    sb = {frozenset((s, d)) for s, d in _pairs(b, tick)}
    union = sa | sb
    return len(sa & sb) / len(union) if union else float("nan")


def connected_components(graph: NetworkGraph, tick: int | None = None) -> list[set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for node in graph.nodes:
        adj[node]
    for src, dst in _pairs(graph, tick):
        adj[src].add(dst)
        if not graph.directed:
            adj[dst].add(src)
    seen: set[str] = set()
    comps: list[set[str]] = []
    for node in sorted(adj):
        if node in seen:
            continue
        queue = deque([node])
        comp: set[str] = set()
        while queue:
            cur = queue.popleft()
            if cur in comp:
                continue
            comp.add(cur)
            queue.extend(adj[cur] - comp)
        seen |= comp
        comps.append(comp)
    return comps


def avg_shortest_path_length(
    graph: NetworkGraph, tick: int | None = None, *, largest_component_only: bool = True
) -> float:
    """平均最短路（BFS）。不连通对的处理策略必须显式给出（Q-13）。"""
    adj: dict[str, set[str]] = defaultdict(set)
    for src, dst in _pairs(graph, tick):
        adj[src].add(dst)
        if not graph.directed:
            adj[dst].add(src)
    comps = connected_components(graph, tick)
    targets = [max(comps, key=len)] if largest_component_only and comps else comps
    total, count = 0, 0
    for comp in targets:
        for start in sorted(comp):
            dist: dict[str, int] = {start: 0}
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                for nxt in adj[cur]:
                    if nxt not in dist:
                        dist[nxt] = dist[cur] + 1
                        queue.append(nxt)
            for node, d in dist.items():
                if node != start:
                    total += d
                    count += 1
    return total / count if count else float("nan")
