"""执行引擎：把请求交给已注册的动力学模型，并计量。

引擎**不持有任何模型语义**：不知道意见维度、不知道更新方程、不知道
指标口径。它只负责请求分发、计时与产物组装。
"""
from .runner import RunRecord, run_many, run_once

__all__ = ["run_once", "run_many", "RunRecord"]
