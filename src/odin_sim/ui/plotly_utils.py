"""UI 用的 plotly 渲染辅助（纯展示，不计算任何统计量）。

图的全部数值都已由 ``analysis`` 层算好（mean / ci_low / ci_high），
此处只负责把它们摆成交互图。这是 L4 的本职——编排与展示，而非重算。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import plotly.graph_objects as go

# 分组曲线的循环配色（plotly 默认色板之外再补一套，保证多组可分辨）。
_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def metric_figure(
    rows: Sequence[Mapping[str, Any]],
    x_key: str,
    metrics: Sequence[str],
    hue_key: str | None = None,
) -> go.Figure:
    """终值对比图：横轴 ``x_key``，每条线一个指标（可选按 ``hue_key`` 分色）。"""
    fig = go.Figure()
    if hue_key:
        hue_vals = sorted({str(r.get(hue_key, "")) for r in rows})
        for hi, hv in enumerate(hue_vals):
            color = _PALETTE[hi % len(_PALETTE)]
            for m in metrics:
                sub = sorted(
                    (r for r in rows if str(r.get(hue_key, "")) == hv),
                    key=lambda r: _sort_key(r.get(x_key)),
                )
                xs = [_sort_key(r.get(x_key)) for r in sub]
                ys = [r.get(f"{m}.mean") for r in sub]
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers",
                    name=f"{m} · {hue_key}={hv}",
                    line=dict(color=color),
                ))
    else:
        for m in metrics:
            sub = sorted(rows, key=lambda r: _sort_key(r.get(x_key)))
            xs = [_sort_key(r.get(x_key)) for r in sub]
            ys = [r.get(f"{m}.mean") for r in sub]
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=m))
    fig.update_layout(
        xaxis_title=x_key,
        yaxis_title="均值（带 95% CI 见误差）",
        legend_title_text="指标 / 分组",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=30, b=40),
    )
    return fig


def timeseries_figure(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    group_by: Sequence[str],
) -> go.Figure:
    """演化曲线：每条 (分组, 序列列) 一条均值线 + 半透明置信带。"""
    fig = go.Figure()
    for ri, row in enumerate(rows):
        if group_by:
            label = " · ".join(str(row.get(k, "")) for k in group_by)
        else:
            label = "all"
        step = np.asarray(row["step"], dtype=float)
        color = _PALETTE[ri % len(_PALETTE)]
        for col in columns:
            mean = np.asarray(row[f"{col}.mean"], dtype=float)
            low = np.asarray(row[f"{col}.ci_low"], dtype=float)
            high = np.asarray(row[f"{col}.ci_high"], dtype=float)
            name = f"{col} ({label})" if group_by else col
            # 置信带：上界正向 + 下界反向闭合，fill=toself 形成带状区域。
            fig.add_trace(go.Scatter(
                x=np.concatenate([step, step[::-1]]),
                y=np.concatenate([high, low[::-1]]),
                fill="toself", mode="lines", line=dict(width=0),
                fillcolor="rgba(31,119,180,0.18)",
                name=f"{name} CI", showlegend=False, hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=step, y=mean, mode="lines", name=name,
                line=dict(color=color),
            ))
    fig.update_layout(
        xaxis_title="时间步 (step)",
        yaxis_title="序列值（均值 ± CI）",
        legend_title_text="序列 / 分组",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=30, b=40),
    )
    return fig


def _sort_key(value: Any) -> Any:
    """缺失或非数值时落到排序友好的字符串，保证 x 轴可排序。"""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return str(value)


def figure_to_html(fig: go.Figure, include_plotlyjs: str = "cdn") -> str:
    """把交互图导出为自包含 HTML 字符串（无需 kaleido，离线可打开）。

    用于 UI 的「下载图 (HTML)」按钮；研究者可在浏览器中继续缩放/悬停。
    若需位图（PNG），应另装 ``kaleido`` 后调 ``fig.to_image``。
    """
    return fig.to_html(include_plotlyjs=include_plotlyjs, full_html=False)


def compare_figure(
    rows: Sequence[Mapping[str, Any]],
    x_key: str,
    metric: str,
    sweep_key: str = "sweep",
) -> go.Figure:
    """多 sweep 对比：横轴 ``x_key``，y = ``metric`` 均值，按 sweep 名称分色叠加。

    每个 sweep 一条均值线 + 误差棒（聚合层给出 CI 时）；缺失 CI 则只画均值线。
    数据全部来自 ``analysis.aggregate`` 的输出，本函数只负责渲染。
    """
    fig = go.Figure()
    sweeps = sorted({str(r.get(sweep_key, "")) for r in rows})
    y_field = f"{metric}.mean"
    low_field = f"{metric}.ci_low"
    high_field = f"{metric}.ci_high"
    for si, sw in enumerate(sweeps):
        color = _PALETTE[si % len(_PALETTE)]
        sub = sorted(
            (r for r in rows if str(r.get(sweep_key, "")) == sw),
            key=lambda r: _sort_key(r.get(x_key)),
        )
        xs = [_sort_key(r.get(x_key)) for r in sub]
        ys = [r.get(y_field) for r in sub]
        lows = [r.get(low_field) for r in sub]
        highs = [r.get(high_field) for r in sub]
        has_ci = all(
            v is not None for v in lows + highs if v is not None
        ) and all(v is not None for v in lows) and all(v is not None for v in highs)
        trace: dict = dict(
            x=xs, y=ys, mode="lines+markers", name=sw, line=dict(color=color)
        )
        if has_ci:
            trace["error_y"] = dict(
                type="data", symmetric=False,
                array=[h - y for h, y in zip(highs, ys)],
                arrayminus=[y - l for l, y in zip(lows, ys)],
            )
        fig.add_trace(go.Scatter(**trace))
    fig.update_layout(
        xaxis_title=x_key,
        yaxis_title=f"{metric}（均值 ± CI）",
        legend_title_text="sweep",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=30, b=40),
    )
    return fig
