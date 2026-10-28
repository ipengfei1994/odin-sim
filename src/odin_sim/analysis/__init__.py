"""实验结果分析层。

v1 范围：消费一次 sweep 产出的 results.csv，做跨条件聚合与置信区间，
并把聚合结果渲染成静态图。

分层
----
- ``aggregate``：统计量本身，只依赖 numpy（平台核心能力）。
- ``figures``：把已算好的统计量画出来，依赖 matplotlib（``[analysis]``
  可选依赖）。渲染层不重算任何统计量，保证图与表口径一致。
"""
from __future__ import annotations

from .aggregate import (
    aggregate,
    aggregate_to_csv,
    estimate_ci,
    filter_by_role,
    load_results,
)
from .figures import (
    MetricColumns,
    plot_bars,
    plot_curves,
    plot_heatmap,
    resolve_metric_columns,
)

__all__ = [
    "MetricColumns",
    "aggregate",
    "aggregate_to_csv",
    "estimate_ci",
    "filter_by_role",
    "load_results",
    "plot_bars",
    "plot_curves",
    "plot_heatmap",
    "resolve_metric_columns",
]
