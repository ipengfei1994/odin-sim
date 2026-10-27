"""实验层：规格展开、批量执行与产物汇总。

分层关系::

    experiments/spec.py    —— 配置 -> 运行列表（确定性、内容寻址）
    experiments/runner.py  —— 调度 engine.run_once、落盘、断点续跑
    storage/               —— 产物命名与序列化

实验层不持有数值逻辑：它调用已注册的动力学模型与指标，自己不做任何更新。
"""
from __future__ import annotations

from .runner import SweepError, SweepResult, run_sweep
from .spec import RunSpec, SpecError, SweepSpec, expand_sweep, sweep_from_json

__all__ = [
    "RunSpec",
    "SweepSpec",
    "SpecError",
    "expand_sweep",
    "sweep_from_json",
    "run_sweep",
    "SweepResult",
    "SweepError",
]
