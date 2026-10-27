"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .adapters.baseline_g3 import BaselineError, load_kernel
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
            print(f"  {run.run_id}  {run.config_id} seed={run.seed} "
                  f"scenario={run.scenario} overrides={run.param_overrides}")
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
                   help="要计算的指标，逗号分隔；'all' 表示所有不需要对照的指标；"
                        "缺省表示不计算指标")
    s.add_argument("--dry-run", action="store_true",
                   help="只展开并打印运行列表，不执行")
    s.add_argument("--show", type=int, default=20, help="dry-run 最多打印多少条")
    s.add_argument("--quiet", action="store_true", help="不打印逐条进度")

    return parser


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
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
