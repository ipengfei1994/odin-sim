"""把平台拓扑注入 baseline 内核（W2 的解耦关键）。

背景
----
外部 G3 内核的 ``model`` 模块在模块作用域写了 ``from .topology import
build_network``，并直接调用它。这意味着**不替换这个引用，平台就无法让内核
跑在 ws / sbm 上**——这是 W1-c 登记的限制之一，本模块解决它。

安全性
------
1. 只替换 ``model_module.build_network`` 这一个名字，不修改内核任何文件；
2. 用上下文管理器，退出时**必定**还原（``finally``）；
3. 进入与退出时各清一次 baseline 的 ``_NET_CACHE``——该缓存的键不含
   ws/sbm 的新参数，不清会在换参数后返回旧图；
4. 注入后 ba / er / ring 的行为必须与注入前**逐位一致**，由
   ``tests/regression/test_topology_baseline_parity.py`` 验证。

限制
----
直接改模块属性是**进程全局**的，不是线程安全的。并行执行实验时不要
在同一个进程里用不同拓扑参数并发注入。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..adapters.baseline_g3 import BaselineKernel
from .generators import build_network

__all__ = ["patched_baseline_topology"]


def _clear_net_cache(kernel: BaselineKernel) -> None:
    cache = getattr(kernel.model_module, "_NET_CACHE", None)
    if isinstance(cache, dict):
        cache.clear()


@contextmanager
def patched_baseline_topology(kernel: BaselineKernel,
                              **options) -> Iterator[None]:
    """在作用域内让 baseline 使用平台拓扑生成器。

    ``options`` 作为拓扑参数的默认值传入（如 ``beta=0.1``、``n_blocks=4``）。
    """
    module = kernel.model_module
    original = module.build_network

    def _injected(n: int, seed: int, kind: str = "ba", m: int = 3,
                  signs=None, homophily: float = 0.0):
        return build_network(n, seed, kind, m,
                             signs=signs, homophily=homophily, **options)

    _clear_net_cache(kernel)
    module.build_network = _injected
    try:
        yield
    finally:
        module.build_network = original
        _clear_net_cache(kernel)
