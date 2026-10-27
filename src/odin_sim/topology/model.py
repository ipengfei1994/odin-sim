"""把平台拓扑生成器包装成 ``TopologyModel`` 协议实现。

与 ``G3Topology`` 的区别：本实现**不依赖 baseline**，支持全部五种拓扑，
包括 baseline 没有的 ws / sbm。新实验应优先用它。
"""
from __future__ import annotations

from ..model.protocol import Network, NetworkSpec
from .generators import SUPPORTED_KINDS, build_network

__all__ = ["PlatformTopology", "TOPOLOGY_NAME"]

TOPOLOGY_NAME = "platform-v1"

# 属于拓扑本身的选项（其余会透传给 build_network 作为拓扑参数）
_KNOWN_OPTIONS = ("beta", "n_blocks", "ratio")


class PlatformTopology:
    """平台原生拓扑生成器（ba / er / ring / ws / sbm）。"""

    name = TOPOLOGY_NAME

    def build(self, spec: NetworkSpec) -> Network:
        if spec.kind not in SUPPORTED_KINDS:
            raise ValueError(
                f"不支持的拓扑：{spec.kind}；支持：{SUPPORTED_KINDS}"
            )
        options = spec.options or {}
        net = build_network(
            spec.n,
            spec.seed,
            spec.kind,
            int(options.get("m", 3)),
            signs=options.get("signs"),
            homophily=float(options.get("homophily", 0.0)),
            **{k: options[k] for k in _KNOWN_OPTIONS if k in options},
        )
        return Network(
            src=net["src"],
            dst=net["dst"],
            deg=net["deg"],
            n_edges=net["n_edges"],
            hash=net["hash"],
            extra={
                "cross_edge_ratio": net.get("cross_edge_ratio", float("nan")),
                "kind": spec.kind,
            },
        )
