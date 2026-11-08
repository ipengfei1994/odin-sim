"""S4｜原始观测量与计算辅助（**不产出 v2 指标数值**）。

本模块的函数一律以 ``observable`` / 描述性名称落盘，不冠以 ``M1-BC``、
``3A-Q`` 等 v2 指标名，也不做「达标 / 未达标」判定。它们的用途是：
在 S0 定义锁定之前，让数据通道可被验证、让研究者能看到已采集到什么；
定义锁定后由 ``DefinitionRegistry`` 登记的函数消费这些输入。
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping, Sequence

from ..networks import (
    NetworkGraph, avg_shortest_path_length, cross_edge_ratio, degree_sequence,
    density, edge_count, jaccard_overlap,
)

__all__ = ["pairwise_distance", "observables", "ObservableError"]


class ObservableError(ValueError):
    """观测量计算失败。"""


def pairwise_distance(values: Sequence[float], *, norm: str) -> float:
    """v2 规则三的**分歧**度量：``D = 2/(N(N-1)) · Σ_{i<j} |x_i − x_j|``。

    ``norm`` 必须由配置显式给出（Q-01 / Q-42）：v2 未规定范数，平台不代选。
    本函数只是示例定义用到的计算辅助，不代表 BC 或其他序参量口径。
    """
    if norm not in ("l1", "l2"):
        raise ObservableError(
            f"意见范数未指定或非法：{norm!r}；必须由配置显式给出 l1/l2（Q-42）"
        )
    xs = [float(v) for v in values]
    n = len(xs)
    if n < 2:
        return float("nan")
    total = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            total += abs(xs[i] - xs[j])
    return 2.0 * total / (n * (n - 1))


def observables(
    records: Mapping[str, Sequence[Any]],
    graphs: Mapping[str, NetworkGraph],
    *,
    config: Any = None,
) -> dict[str, Any]:
    """把已落盘的记录/网络压缩成可审阅的观测量清单。

    设计约束：只做**计数、比例、直接可推导的图论量**；不做聚类、不做
    双峰判定、不做模块度、不做因果推断——那都属于 v2 指标，定义未锁定。
    """
    out: dict[str, Any] = {}

    # --- 记录量 ---------------------------------------------------------
    for key in (
        "agent_snapshots", "contents", "exposures", "interactions",
        "decisions", "transitions", "influences", "network_snapshots",
        "community_partition_result",
    ):
        out[f"count_{key}"] = len(records.get(key) or ())

    exposures = list(records.get("exposures") or ())
    out["count_exposures_selected"] = sum(1 for e in exposures if e.selected)
    out["count_exposures_visible_ge_1s"] = sum(
        1 for e in exposures if e.in_viewport and e.visible_seconds >= 1.0
    )
    decisions = list(records.get("decisions") or ())
    out["count_decisions_executed"] = sum(
        1 for d in decisions if d.execution_status == "executed"
    )
    out["count_decisions_rejected"] = sum(
        1 for d in decisions if d.execution_status == "rejected"
    )
    interactions = list(records.get("interactions") or ())
    out["action_type_counts"] = dict(sorted(Counter(
        i.action_type for i in interactions
    ).items()))
    out["selection_mode_counts"] = dict(sorted(Counter(
        e.selection_mode for e in exposures
    ).items()))
    influences = list(records.get("influences") or ())
    out["influence_relation_kind_counts"] = dict(sorted(Counter(
        r.relation_kind for r in influences
    ).items()))

    snapshots = list(records.get("agent_snapshots") or ())
    if snapshots:
        final_tick = max(s.tick for s in snapshots)
        finals = [s.opinion_true[0] for s in snapshots
                  if s.tick == final_tick and s.opinion_true]
        if finals:
            mean = sum(finals) / len(finals)
            var = sum((x - mean) ** 2 for x in finals) / len(finals)
            out["opinion_final_mean"] = mean
            out["opinion_final_std"] = math.sqrt(var)
            out["opinion_final_n"] = len(finals)
        projected = [s.opinion_projected[0] for s in snapshots
                     if s.tick == final_tick and s.opinion_projected]
        if projected and finals:
            out["projection_abs_bias_mean"] = sum(
                abs(p - t) for p, t in zip(projected, finals)
            ) / min(len(projected), len(finals))

    # --- 网络量 ---------------------------------------------------------
    partition_records = list(records.get("community_partition_result") or ())
    partition = partition_records[-1].node_to_community if partition_records else {}
    for kind, graph in graphs.items():
        out[f"{kind}_node_count"] = len(graph.nodes)
        out[f"{kind}_edge_count"] = edge_count(graph)
        out[f"{kind}_density"] = density(graph)
        deg = degree_sequence(graph)
        out[f"{kind}_degree_max"] = max(deg.values()) if deg else 0
        if partition:
            out[f"{kind}_cross_edge_ratio"] = cross_edge_ratio(graph, partition)
    if "potential" in graphs:
        out["potential_avg_shortest_path_lcc"] = avg_shortest_path_length(
            graphs["potential"], largest_component_only=True
        )
        for other in ("exposure", "influence", "interaction"):
            if other in graphs:
                out[f"potential_{other}_jaccard"] = jaccard_overlap(
                    graphs["potential"], graphs[other]
                )
    if "exposure" in graphs and "influence" in graphs:
        out["exposure_influence_jaccard"] = jaccard_overlap(
            graphs["exposure"], graphs["influence"]
        )

    # --- 采样与配置回显（可追溯，不作判定） -----------------------------
    if config is not None:
        out["population_size"] = int(config.population_size)
        out["simulation_steps"] = int(config.simulation_steps)
        out["sampling_interval_k"] = int(config.sampling_interval_k)
        out["community_partition_method"] = config.community_partition_method
        out["metric_def_version"] = config.definition_version.metric_def_version
        out["thresholds_locked"] = config.definition_version.thresholds_locked
    return out
