"""把聚合结果渲染成静态图（可选依赖 matplotlib）。

与 :mod:`odin_sim.analysis.aggregate` 的分工
-------------------------------------------
``aggregate`` 负责统计量（只依赖 numpy，属平台核心能力）；本模块只负责把**已经
算好的统计量**画出来（依赖 matplotlib，属 ``[analysis]`` 可选依赖）。

**渲染层不做统计推断，也不重算聚合值**：只读 aggregate 产出的
``<metric>.mean / .ci_low / .ci_high``（数值指标）或
``<metric>.rate / .rate_ci_low / .rate_ci_high``（布尔指标的发生率）。
这样"统计量是什么口径"只有一处定义，图与表不会各说一套。

设计约束
--------
1. **matplotlib 惰性导入**：未安装时给出明确的安装引导，而不是在 import 期崩掉。
   否则没装 ``[analysis]`` 的用户连纯 numpy 的 ``analyze`` 都用不了，可选依赖
   就变成了事实上的强依赖。
2. **不使用中文字体**：Linux CI 通常没有中文字体，硬写中文标题会渲染成豆腐块。
   默认标签一律取列名（数据自带的英文标识），标题由调用方自传，字体责任交给
   调用方。
3. **纯函数式**：输入是聚合好的行，不读文件、不跑仿真、不改动输入。
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期生效
    from matplotlib.figure import Figure

__all__ = [
    "MetricColumns",
    "plot_bars",
    "plot_curves",
    "plot_heatmap",
    "resolve_metric_columns",
]

Rows = Sequence[Mapping[str, Any]]

_NUMERIC = "numeric"
_RATE = "rate"


@dataclass(frozen=True)
class MetricColumns:
    """聚合表里某个指标的三列：点估计 + 区间两端。"""

    kind: str
    """``numeric``（均值 CI）或 ``rate``（发生率区间）。"""

    value: str
    """点估计列名。"""

    low: str
    """区间下界列名。"""

    high: str
    """区间上界列名。"""

    @property
    def is_rate(self) -> bool:
        return self.kind == _RATE

    def label(self, metric: str) -> str:
        """给纵轴的默认名称。"""
        return f"{metric} rate" if self.is_rate else metric


def _import_matplotlib(backend: str | None = "Agg"):
    """惰性导入 pyplot。未安装 matplotlib 时给出可执行的引导。"""
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "画图需要 matplotlib，请用 `uv sync --extra analysis` 安装可选依赖组"
        ) from exc
    # pyplot 一旦被别处导入，再切 backend 会被忽略甚至报错，故只在未导入时设置
    if backend and "matplotlib.pyplot" not in sys.modules:
        matplotlib.use(backend)
    from matplotlib import pyplot as plt

    return plt


def _has(rows: Rows, key: str) -> bool:
    return any(r.get(key) is not None for r in rows)


def _require_column(rows: Rows, key: str) -> None:
    if not any(key in r for r in rows):
        available = sorted({k for r in rows for k in r})
        raise KeyError(f"聚合结果里没有列 {key!r}。可用列：{available}")


def resolve_metric_columns(rows: Rows, metric: str) -> MetricColumns:
    """推断某个指标在聚合表中对应的点估计与区间列。

    数值指标走 ``mean/ci_low/ci_high``；布尔指标（如"是否反噬"）在聚合层已被
    折算成发生率，走 ``rate/rate_ci_low/rate_ci_high``。两者都不存在时报错并
    列出可用列，绝不静默画一张空图。
    """
    if not rows:
        raise ValueError("聚合结果为空，无法推断指标列")
    mean_key = f"{metric}.mean"
    rate_key = f"{metric}.rate"
    if _has(rows, mean_key):
        return MetricColumns(_NUMERIC, mean_key, f"{metric}.ci_low", f"{metric}.ci_high")
    if _has(rows, rate_key):
        return MetricColumns(
            _RATE, rate_key, f"{metric}.rate_ci_low", f"{metric}.rate_ci_high"
        )
    available = sorted({k for r in rows for k in r if k.startswith(f"{metric}.")})
    raise KeyError(
        f"指标 {metric!r} 没有可用的点估计列（期望 {mean_key} 或 {rate_key}）。"
        f"该指标下的列：{available or '（无）'}"
    )


def _finish(fig: Figure, out: str | Path | None) -> Figure:
    if out is not None:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=150, bbox_inches="tight")
    return fig


def _split_by(rows: Rows, hue: str | None) -> list[tuple[str, list[Mapping]]]:
    """按 hue 分组，保持首次出现顺序；hue 为空时整体作为一组。"""
    if hue is None:
        return [("", list(rows))]
    buckets: dict[str, list[Mapping]] = {}
    order: list[str] = []
    for r in rows:
        label = str(r.get(hue, ""))
        if label not in buckets:
            buckets[label] = []
            order.append(label)
        buckets[label].append(r)
    return [(label, buckets[label]) for label in order]


def _sorted_points(
    recs: Sequence[Mapping], x: str, cols: MetricColumns
) -> tuple[list[tuple], bool]:
    """取出 (x_raw, value, low, high) 并排序。返回 (点集, x 是否数值轴)。

    点估计缺失的行直接跳过——聚合层已经用 n=0 标出了空组，这里不画点也不编造。
    """
    pts: list[tuple] = []
    for r in recs:
        raw_x = r.get(x)
        value = r.get(cols.value)
        if raw_x is None or value is None:
            continue
        pts.append((raw_x, float(value), _opt(r.get(cols.low)), _opt(r.get(cols.high))))
    if not pts:
        return [], False
    try:
        pts.sort(key=lambda p: float(p[0]))
        numeric = True
    except (TypeError, ValueError):
        pts.sort(key=lambda p: str(p[0]))
        numeric = False
    return pts, numeric


def _opt(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def plot_curves(
    rows: Rows,
    *,
    x: str,
    metric: str,
    hue: str | None = None,
    out: str | Path | None = None,
    ax: Any = None,
    backend: str | None = "Agg",
    figsize: tuple[float, float] = (6.4, 4.0),
    title: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    marker: str = "o",
    band_alpha: float = 0.2,
    grid: bool = True,
) -> Figure:
    """条件—效应曲线：横轴一个分组维度，纵轴指标点估计，置信区间画成半透明带。

    参数
    ----
    rows : ``aggregate`` 的输出（每行一个分组）。
    x : 横轴分组键（如 ``beta_gov``）。能解析成数字就按数值排，否则按类别等距排。
    metric : 指标列名（不带后缀），如 ``all_extreme_final`` 或 ``g3.backfire.bf_final``。
    hue : 分组键（如 ``mode``）；给一个值画一条线。不给则画一条。
    out : 图片落盘路径；不给则只返回 Figure，不写文件。
    """
    plt = _import_matplotlib(backend)
    cols = resolve_metric_columns(rows, metric)
    _require_column(rows, x)
    if hue is not None:
        _require_column(rows, hue)

    fig = None
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    ticks: tuple[list[float], list[str]] | None = None
    any_data = False
    for label, recs in _split_by(rows, hue):
        pts, numeric = _sorted_points(recs, x, cols)
        if not pts:
            continue
        any_data = True
        xs_plot = [float(p[0]) for p in pts] if numeric else list(range(len(pts)))
        ys = [p[1] for p in pts]
        ax.plot(xs_plot, ys, marker=marker, label=(label if hue is not None else None))
        band = [
            (xv, p[2], p[3])
            for xv, p in zip(xs_plot, pts)
            if p[2] is not None and p[3] is not None
        ]
        if band:
            ax.fill_between(
                [b[0] for b in band],
                [b[1] for b in band],
                [b[2] for b in band],
                alpha=band_alpha,
            )
        if not numeric and ticks is None:
            ticks = (list(xs_plot), [str(p[0]) for p in pts])

    if not any_data:
        raise ValueError(f"没有可绘制的数据：指标 {metric!r} 在所给分组下的点估计全为空")

    if ticks is not None:
        ax.set_xticks(ticks[0])
        ax.set_xticklabels(ticks[1])
    if hue is not None:
        handles, _ = ax.get_legend_handles_labels()
        if handles:
            ax.legend(title=hue)
    ax.set_xlabel(xlabel if xlabel is not None else x)
    ax.set_ylabel(ylabel if ylabel is not None else cols.label(metric))
    if title:
        ax.set_title(title)
    if grid:
        ax.grid(True, alpha=0.3)
    return _finish(fig, out)


def plot_bars(
    rows: Rows,
    *,
    x: str,
    metric: str,
    hue: str | None = None,
    out: str | Path | None = None,
    ax: Any = None,
    backend: str | None = "Agg",
    figsize: tuple[float, float] = (6.4, 4.0),
    title: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    capsize: float = 3.0,
    grid: bool = True,
) -> Figure:
    """分组柱状图：类别条件下的点估计 + 置信区间误差棒（类别比较用）。

    误差棒用非对称形式（``yerr=[下, 上]``），因为比例的 CI 被裁剪过、均值的 CI
    在小样本下也可能不对称于点估计，硬用对称棒会画错。
    """
    plt = _import_matplotlib(backend)
    cols = resolve_metric_columns(rows, metric)
    _require_column(rows, x)
    if hue is not None:
        _require_column(rows, hue)

    fig = None
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    groups = _split_by(rows, hue)
    # 横轴位置由 x 的取值决定，各 hue 组并排错开
    x_values: list[str] = []
    for _, recs in groups:
        for r in recs:
            label = str(r.get(x, ""))
            if label not in x_values:
                x_values.append(label)
    if not x_values:
        raise ValueError(f"没有可绘制的数据：列 {x!r} 在所给分组下全为空")

    width = 0.8 / max(1, len(groups))
    any_data = False
    for gi, (label, recs) in enumerate(groups):
        centers, heights, err_low, err_high = [], [], [], []
        by_x = {str(r.get(x, "")): r for r in recs}
        for pos, xv in enumerate(x_values):
            r = by_x.get(xv)
            if r is None or r.get(cols.value) is None:
                continue
            value = float(r.get(cols.value))
            lo = _opt(r.get(cols.low))
            hi = _opt(r.get(cols.high))
            centers.append(pos + (gi - (len(groups) - 1) / 2) * width)
            heights.append(value)
            err_low.append(max(0.0, value - lo) if lo is not None else 0.0)
            err_high.append(max(0.0, hi - value) if hi is not None else 0.0)
        if not centers:
            continue
        any_data = True
        ax.bar(
            centers, heights, width=width,
            label=(label if hue is not None else None),
            yerr=[err_low, err_high], capsize=capsize,
        )
    if not any_data:
        raise ValueError(f"没有可绘制的数据：指标 {metric!r} 在所给分组下的点估计全为空")

    ax.set_xticks(range(len(x_values)))
    ax.set_xticklabels(x_values)
    if hue is not None:
        handles, _ = ax.get_legend_handles_labels()
        if handles:
            ax.legend(title=hue)
    ax.set_xlabel(xlabel if xlabel is not None else x)
    ax.set_ylabel(ylabel if ylabel is not None else cols.label(metric))
    if title:
        ax.set_title(title)
    if grid:
        ax.grid(True, axis="y", alpha=0.3)
    return _finish(fig, out)


def plot_heatmap(
    rows: Rows,
    *,
    x: str,
    y: str,
    metric: str,
    out: str | Path | None = None,
    ax: Any = None,
    backend: str | None = "Agg",
    figsize: tuple[float, float] = (6.4, 4.4),
    title: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    cmap: str = "viridis",
    annotate: bool = True,
    max_annotate_cells: int = 64,
) -> Figure:
    """相图：两个实验维度张成参数平面，单元格颜色是指标的聚合值。

    用于判断效应在参数平面上是否有分区（例如治理强度与延迟谁主导结果）。
    缺失组合留白，不插值填充——空白处表示"没跑过"，不是"值为零"。
    """
    plt = _import_matplotlib(backend)
    import numpy as np

    cols = resolve_metric_columns(rows, metric)
    _require_column(rows, x)
    _require_column(rows, y)

    xs = _ordered_labels(rows, x)
    ys = _ordered_labels(rows, y)
    if not xs or not ys:
        raise ValueError(f"没有可绘制的数据：{x!r} 或 {y!r} 的取值全为空")

    x_index = {label: i for i, label in enumerate(xs)}
    y_index = {label: i for i, label in enumerate(ys)}
    mat = np.full((len(ys), len(xs)), np.nan, dtype=float)
    for r in rows:
        xv, yv = str(r.get(x, "")), str(r.get(y, ""))
        value = r.get(cols.value)
        if xv not in x_index or yv not in y_index or value is None:
            continue
        mat[y_index[yv], x_index[xv]] = float(value)
    if np.all(np.isnan(mat)):
        raise ValueError(f"没有可绘制的数据：指标 {metric!r} 在该平面上全部缺失")

    fig = None
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    im = ax.imshow(
        np.ma.masked_invalid(mat), origin="lower", aspect="auto", cmap=cmap,
    )
    ax.set_xticks(range(len(xs)), xs, rotation=45 if max(len(s) for s in xs) > 4 else 0,
                  ha="right" if max(len(s) for s in xs) > 4 else "center")
    ax.set_yticks(range(len(ys)), ys)
    ax.set_xlabel(xlabel if xlabel is not None else x)
    ax.set_ylabel(ylabel if ylabel is not None else y)
    if title:
        ax.set_title(title)

    if annotate and mat.size <= max_annotate_cells:
        for iy in range(len(ys)):
            for ix in range(len(xs)):
                value = mat[iy, ix]
                if np.isnan(value):
                    continue
                ax.text(ix, iy, f"{value:.3g}", ha="center", va="center", fontsize=8,
                        color="white" if value > np.nanmedian(mat) else "black")

    fig.colorbar(im, ax=ax, label=cols.label(metric))
    return _finish(fig, out)


def _ordered_labels(rows: Rows, key: str) -> list[str]:
    """某个维度的取值按数值序（能转数字时）或字典序去重返回字符串标签。"""
    values = [r.get(key) for r in rows]
    values = [v for v in values if v is not None]
    if not values:
        return []
    try:
        uniq = sorted(set(values), key=lambda v: float(v))
    except (TypeError, ValueError):
        uniq = sorted(set(values), key=lambda v: str(v))
    return [str(v) for v in uniq]
