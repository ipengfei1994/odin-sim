"""实验结果分析层。

v1 范围：消费一次 sweep 产出的 results.csv，做跨条件聚合与置信区间。
可视化（相图/曲线）与更复杂统计留待后续阶段。
"""
from __future__ import annotations

from .aggregate import (
    aggregate,
    aggregate_to_csv,
    estimate_ci,
    load_results,
)

__all__ = [
    "aggregate",
    "aggregate_to_csv",
    "estimate_ci",
    "load_results",
]
