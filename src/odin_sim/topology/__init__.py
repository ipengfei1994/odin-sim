"""拓扑层：网络生成（平台侧原生实现）+ 注入 baseline 的适配。

对外只需两件事：

* ``build_network`` —— 生成 ba / er / ring / ws / sbm 五种网络；
* ``patched_baseline_topology`` —— 让 baseline 内核使用平台生成器。

新增拓扑 ``ws`` / ``sbm`` 的参数默认值属于**研究假设**，登记在
``docs/topology-assumptions.md``（A-1 / A-2），尚未经课题组确认。
"""
from .generators import (
    DEFAULT_SBM_BLOCKS,
    DEFAULT_SBM_RATIO,
    DEFAULT_WS_BETA,
    SUPPORTED_KINDS,
    build_network,
    network_hash,
)
from .inject import patched_baseline_topology

__all__ = [
    "SUPPORTED_KINDS",
    "DEFAULT_WS_BETA",
    "DEFAULT_SBM_BLOCKS",
    "DEFAULT_SBM_RATIO",
    "build_network",
    "network_hash",
    "patched_baseline_topology",
]
