"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .adapters.baseline_g3 import BaselineError, load_kernel
from .analysis import (
    aggregate,
    aggregate_timeseries,
    aggregate_to_csv,
    filter_by_role,
    load_results,
    load_run_series,
    timeseries_to_csv,
)
from .analysis.figures import plot_bars, plot_curves, plot_heatmap, plot_timeseries
from .experiments import (
    SpecError,
    SweepError,
    expand_sweep,
    run_sweep,
    sweep_from_json,
)
from .reproducibility import snapshot
from .verification import read_rows, verify_metric_rows, verify_rows

DEFAULT_PACKAGES = ("numpy",)


def _cmd_info(args: argparse.Namespace) -> int:
    info = snapshot(packages=DEFAULT_PACKAGES, extra={"odin_sim_version": __version__})
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify_baseline(args: argparse.Namespace) -> int:
    try:
        kernel = load_kernel(args.baseline_root)
    except BaselineError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    rows = read_rows(args.data)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    result = verify_rows(kernel, rows, scenario=args.scenario)

    print(f"[verify] baseline={kernel.root}")
    print(f"[verify] model_version={kernel.model_version}")
    print(f"[verify] data={args.data} scenario={args.scenario}")
    print(f"[verify] rows={result.total} matched={result.matched} "
          f"mismatched={len(result.mismatches)}")

    for m in result.mismatches[: args.show]:
        print(f"  [mismatch] row={m.row_index} config={m.config_id} seed={m.seed} "
              f"{m.field_name}: expected={m.expected} actual={m.actual}")
    if len(result.mismatches) > args.show:
        print(f"  ... 另有 {len(result.mismatches) - args.show} 条不一致")

    if not result.ok:
        return 1
    return 0


def _cmd_verify_metrics(args: argparse.Namespace) -> int:
    try:
        kernel = load_kernel(args.baseline_root)
    except BaselineError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    rows = read_rows(args.data)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    result = verify_metric_rows(kernel, rows, scenario=args.scenario)

    print(f"[verify-metrics] baseline={kernel.root}")
    print(f"[verify-metrics] data={args.data} scenario={args.scenario}")
    print(f"[verify-metrics] rows={result.total} matched={result.matched} "
          f"mismatched={len(result.mismatches)}")

    for m in result.mismatches[: args.show]:
        print(f"  [mismatch] row={m.row_index} config={m.config_id} seed={m.seed} "
              f"{m.field_name}: expected={m.expected} actual={m.actual}")
    if len(result.mismatches) > args.show:
        print(f"  ... 另有 {len(result.mismatches) - args.show} 条不一致")

    if not result.ok:
        return 1
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    try:
        spec = sweep_from_json(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, SpecError) as exc:
        print(f"[error] 读取 sweep 配置失败：{exc}", file=sys.stderr)
        return 2

    try:
        runs = expand_sweep(spec)
    except SpecError as exc:
        print(f"[error] 展开 sweep 失败：{exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[sweep] name={spec.name} model={spec.model} "
              f"fingerprint={spec.fingerprint}")
        print(f"[sweep] runs={len(runs)}")
        for run in runs[: args.show]:
            tag = "control" if run.role == "control" else "main   "
            pair = f" control={run.control_run_id}" if run.control_run_id else ""
            print(f"  [{tag}] {run.run_id}  {run.config_id} seed={run.seed} "
                  f"scenario={run.scenario} overrides={run.param_overrides}{pair}")
        if len(runs) > args.show:
            print(f"  ... 另有 {len(runs) - args.show} 条")
        return 0

    metrics = None
    if args.metrics:
        metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]

    try:
        result = run_sweep(spec, out_root=args.out, metrics=metrics, echo=not args.quiet)
    except SweepError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(f"[sweep] name={result.name} fingerprint={result.fingerprint}")
    print(f"[sweep] total={result.total} executed={result.executed} "
          f"skipped={result.skipped} failed={result.failed} "
          f"aborted={result.aborted}")
    if result.results_csv:
        print(f"[sweep] results={result.results_csv}")
    if result.sweep_manifest:
        print(f"[sweep] manifest={result.sweep_manifest}")
    return 0 if result.ok else 1


def _cmd_analyze(args: argparse.Namespace) -> int:
    results = load_results(args.sweep)
    if not results:
        print(f"[analyze] 未读到任何结果行：{args.sweep}/results.csv", file=sys.stderr)
        return 2
    results = filter_by_role(results, args.role)
    if not results:
        print(f"[analyze] 按 role={args.role} 过滤后没有任何结果行", file=sys.stderr)
        return 2

    rows = aggregate(results, args.group_by, args.metric, confidence=args.confidence)

    if args.out:
        path = aggregate_to_csv(
            results, args.group_by, args.metric, args.out, confidence=args.confidence
        )
        print(f"[analyze] 分组数={len(rows)} 指标={list(args.metric)} -> {path}")
        return 0

    import csv
    import io

    fieldnames: list[str] = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})
    print(buf.getvalue(), end="")
    return 0


def _plot_group_by(args: argparse.Namespace) -> list[str]:
    """默认聚合维度：横轴 + 分组 +（热图的纵轴），去重保序。"""
    keys = [args.x]
    if args.hue:
        keys.append(args.hue)
    if args.kind == "heatmap" and args.heatmap_y:
        keys.append(args.heatmap_y)
    out: list[str] = []
    for k in keys:
        if k not in out:
            out.append(k)
    return out


def _cmd_plot(args: argparse.Namespace) -> int:
    results = load_results(args.sweep)
    if not results:
        print(f"[plot] 未读到任何结果行：{args.sweep}/results.csv", file=sys.stderr)
        return 2
    results = filter_by_role(results, args.role)
    if not results:
        print(f"[plot] 按 role={args.role} 过滤后没有任何结果行", file=sys.stderr)
        return 2

    group_by = list(args.group_by) if args.group_by else _plot_group_by(args)
    # 未知列名一律报错，不静默聚成"一个空组"再画一张莫名其妙的图
    available = set(results[0].keys())
    missing = [k for k in [*group_by, args.metric] if k not in available]
    if missing:
        print(
            f"[error] results.csv 中没有这些列：{missing}\n"
            f"        可用列：{sorted(available)}",
            file=sys.stderr,
        )
        return 2

    if args.kind == "heatmap" and not args.heatmap_y:
        print("[error] kind=heatmap 需要 --heatmap-y 指定纵轴分组键", file=sys.stderr)
        return 2

    rows = aggregate(results, group_by, [args.metric], confidence=args.confidence)
    out = args.out or str(Path(args.sweep) / "figures" / f"{args.metric}_{args.kind}.png")

    try:
        if args.kind == "heatmap":
            plot_heatmap(rows, x=args.x, y=args.heatmap_y, metric=args.metric,
                         out=out, title=args.title)
        elif args.kind == "bar":
            plot_bars(rows, x=args.x, metric=args.metric, hue=args.hue,
                      out=out, title=args.title)
        else:
            plot_curves(rows, x=args.x, metric=args.metric, hue=args.hue,
                        out=out, title=args.title)
    except RuntimeError as exc:  # matplotlib 缺失等环境问题
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    except (KeyError, ValueError) as exc:  # 列名/空数据等业务错误
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(f"[plot] kind={args.kind} x={args.x} metric={args.metric} "
          f"group_by={group_by} 分组数={len(rows)} -> {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="odin-sim", description="ODIN-Sim 命令行入口")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="输出运行环境与代码版本快照")

    v = sub.add_parser("verify-baseline", help="对 G3 baseline 数据集做轨迹指纹回归")
    v.add_argument("--baseline-root", default=None,
                   help="外部 G3 baseline 根目录；也可用环境变量 ODIN_BASELINE_ROOT")
    v.add_argument("--data", required=True, help="baseline 的逐次运行 CSV 路径")
    v.add_argument("--scenario", default="polarized", help="场景名（默认 polarized）")
    v.add_argument("--limit", type=int, default=0, help="只校验前 N 行（0 表示全部）")
    v.add_argument("--show", type=int, default=10, help="最多打印多少条不一致详情")

    m = sub.add_parser("verify-metrics", help="对 G3 baseline 数据集做指标等价性回归")
    m.add_argument("--baseline-root", default=None,
                   help="外部 G3 baseline 根目录；也可用环境变量 ODIN_BASELINE_ROOT")
    m.add_argument("--data", required=True, help="baseline 的逐次运行 CSV 路径")
    m.add_argument("--scenario", default="polarized", help="场景名（默认 polarized）")
    m.add_argument("--limit", type=int, default=0, help="只校验前 N 行（0 表示全部）")
    m.add_argument("--show", type=int, default=10, help="最多打印多少条不一致详情")

    s = sub.add_parser("sweep", help="按配置批量运行实验并落盘")
    s.add_argument("--config", required=True, help="sweep 配置 JSON 路径")
    s.add_argument("--out", default="results", help="产物根目录（默认 results）")
    s.add_argument("--metrics", default=None,
                   help="要计算的指标，逗号分隔；'all' 表示全部已注册指标"
                        "（需要配对对照的指标只在配置 control 时才计算）；"
                        "缺省表示不计算指标")
    s.add_argument("--dry-run", action="store_true",
                   help="只展开并打印运行列表，不执行")
    s.add_argument("--show", type=int, default=20, help="dry-run 最多打印多少条")
    s.add_argument("--quiet", action="store_true", help="不打印逐条进度")

    a = sub.add_parser("analyze", help="聚合 sweep 结果并给出置信区间")
    a.add_argument("--sweep", default="results",
                   help="sweep 产物根目录（含 results.csv），默认 results")
    a.add_argument("--group-by", nargs="+", required=True,
                   help="分组键，如 mode beta_gov")
    a.add_argument("--metric", nargs="+", required=True,
                   help="要聚合的指标列名，如 g3.backfire.bf_final")
    a.add_argument("--confidence", type=float, default=0.95,
                   help="CI 置信度，默认 0.95")
    a.add_argument("--role", default="main", choices=["main", "control", "all"],
                   help="只看哪种运行；配了对照时默认 main（对照是配对基线，不是实验条件）")
    a.add_argument("--out", default=None,
                   help="聚合结果输出 CSV；不指定则打印到 stdout（CSV 格式）")

    p = sub.add_parser("plot", help="把 sweep 结果画成图（曲线 / 柱状 / 相图热图）")
    p.add_argument("--sweep", default="results",
                   help="sweep 产物根目录（含 results.csv），默认 results")
    p.add_argument("--x", required=True, help="横轴分组键，如 beta_gov / mode / tau")
    p.add_argument("--metric", required=True,
                   help="纵轴指标列名，如 all_extreme_final")
    p.add_argument("--kind", default="curve", choices=["curve", "bar", "heatmap"],
                   help="图形类型，默认 curve")
    p.add_argument("--hue", default=None,
                   help="分组键（一个取值一条线/一组柱），如 mode")
    p.add_argument("--heatmap-y", default=None,
                   help="kind=heatmap 时的纵轴分组键，如 tau")
    p.add_argument("--group-by", nargs="*", default=None,
                   help="聚合分组键；默认由 x/hue/heatmap-y 推导")
    p.add_argument("--confidence", type=float, default=0.95,
                   help="CI 置信度，默认 0.95")
    p.add_argument("--role", default="main", choices=["main", "control", "all"],
                   help="只看哪种运行；配了对照时默认 main（对照是配对基线，不是实验条件）")
    p.add_argument("--title", default=None, help="图标题")
    p.add_argument("--out", default=None,
                   help="输出图片路径；默认 <sweep>/figures/<metric>_<kind>.png")

    c = sub.add_parser("curves",
                       help="把 sweep 的逐时间步序列画成带置信带的演化曲线")
    c.add_argument("--sweep", default="results",
                   help="sweep 产物根目录（读 runs/<run_id>/timeseries.csv），默认 results")
    c.add_argument("--group-by", nargs="*", default=[],
                   help="分组键，如 beta_gov mode；留空表示所有运行并成一条曲线")
    c.add_argument("--column", nargs="*", default=None,
                   help="序列列名，如 ord_extreme all_joint；留空表示全部可用列")
    c.add_argument("--confidence", type=float, default=0.95,
                   help="CI 置信度，默认 0.95")
    c.add_argument("--role", default="main", choices=["main", "control", "all"],
                   help="只看哪种运行；配了对照时默认 main（对照是配对基线，不是实验条件）")
    c.add_argument("--title", default=None, help="图标题")
    c.add_argument("--out", default=None,
                   help="输出图片路径；默认 <sweep>/figures/timeseries.png")
    c.add_argument("--csv", default=None, help="同时导出长表 CSV（每个时间点一行）")

    u = sub.add_parser("ui", help="启动 Streamlit Web UI（需安装 [ui] 依赖）")
    u.add_argument("--host", default=None,
                   help="绑定主机（默认 streamlit 用 localhost）")
    u.add_argument("--port", type=int, default=None, help="端口（默认 8501）")
    u.add_argument("--no-browser", action="store_true",
                   help="不自动打开浏览器（适合无头/远程服务器）")

    return parser


def _cmd_curves(args: argparse.Namespace) -> int:
    records = load_run_series(args.sweep, role=args.role)
    if not records:
        print(
            f"[curves] 未读到任何时间序列：{args.sweep}/runs/*/timeseries.csv\n"
            "        若这批产物是在本版本之前生成的，重跑 sweep 即可补写该文件。",
            file=sys.stderr,
        )
        return 2

    rows = aggregate_timeseries(
        records, args.group_by, args.column, confidence=args.confidence
    )
    if not rows:
        print("[curves] 聚合结果为空", file=sys.stderr)
        return 2

    if args.csv:
        columns = args.column or sorted(
            k[: -len(".mean")] for k in rows[0] if k.endswith(".mean")
        )
        csv_path = timeseries_to_csv(rows, args.csv, columns)
        print(f"[curves] 长表：{csv_path}")

    name = args.column[0] if args.column else "timeseries"
    out = args.out or str(Path(args.sweep) / "figures" / f"{name}.png")
    plot_timeseries(rows, args.column, title=args.title, out=out)
    print(f"[curves] 组数={len(rows)} 图：{out}")
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    import subprocess
    import sys
    from pathlib import Path

    # 按路径定位 app.py，避免核心/接口层 import ``odin_sim.ui``
    # （L4 分层不变量：见 docs/architecture-v1.md §4.3）。
    app_path = Path(__file__).resolve().parent / "ui" / "app.py"

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.headless", "true" if args.no_browser else "false",
        "--browser.gatherUsageStats", "false",
    ]
    if args.host:
        cmd += ["--server.address", args.host]
    if args.port:
        cmd += ["--server.port", str(args.port)]
    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        print(
            "[error] 未找到 streamlit；请先安装 UI 依赖：uv sync --extra ui",
            file=sys.stderr,
        )
        return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "info":
        return _cmd_info(args)
    if args.command == "verify-baseline":
        return _cmd_verify_baseline(args)
    if args.command == "verify-metrics":
        return _cmd_verify_metrics(args)
    if args.command == "sweep":
        return _cmd_sweep(args)
    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command == "plot":
        return _cmd_plot(args)
    if args.command == "curves":
        return _cmd_curves(args)
    if args.command == "ui":
        return _cmd_ui(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
