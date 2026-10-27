"""批量实验执行器。

职责边界
--------
* 实验层负责：展开、调度、落盘、断点续跑、汇总。
* 引擎负责：单次分发与计时（``engine.run_once``）。
* 模型负责：数值本身。

设计约束
--------
1. **幂等**：同一 sweep 重跑，已完成且规格一致的运行直接跳过；产物目录由
   ``run_id``（规格内容哈希）决定，换目录也能命中。
2. **不吞失败**：运行失败写 status=error 的 manifest 并记异常类型与消息；
   ``on_error="abort"``（默认）时中断 sweep 并保留已完成的结果。
3. **不替指标编造输入**：需要同种子对照的指标（``requires_control=True``）
   本阶段**跳过并记录原因**，不做退化的绝对值判定。
4. **轨迹默认不落盘**：整条轨迹体积大（N×步数×维数），默认只存哈希与序列
   末值；需要时由 ``store_trajectory`` 显式开启。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .. import __version__
from ..engine.runner import run_once
from ..model.protocol import MetricContext
from ..model.registry import DYNAMICS, METRICS
from ..reproducibility import snapshot
from ..storage import layout
from ..storage.manifest import (
    STATUS_ERROR,
    STATUS_OK,
    read_manifest,
    write_manifest,
    write_results_csv,
    write_sweep_manifest,
)
from .spec import RunSpec, SweepSpec, expand_sweep

__all__ = ["SweepError", "SweepResult", "run_sweep", "resolve_metrics"]


class SweepError(RuntimeError):
    """sweep 执行被中断或无法开始。"""


@dataclass
class SweepResult:
    """一次 sweep 的执行结果。"""

    name: str
    out_root: Path
    fingerprint: str
    total: int
    executed: int = 0
    skipped: int = 0
    failed: int = 0
    aborted: bool = False
    rows: list[dict[str, Any]] = field(default_factory=list)
    results_csv: Path | None = None
    sweep_manifest: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.aborted and self.failed == 0


def resolve_metrics(names: Sequence[str] | None) -> tuple[list[Any], list[dict[str, str]]]:
    """解析指标名。

    返回 ``(待计算指标, 被跳过的指标及原因)``。``names`` 为 None 时不计算指标。
    """
    if names is None:
        return [], []

    if len(names) == 1 and names[0] == "all":
        names = METRICS.names()

    metrics = []
    skipped: list[dict[str, str]] = []
    for name in names:
        metric = METRICS.get(name)
        if getattr(metric, "requires_control", False):
            skipped.append({
                "name": name,
                "reason": "需要同种子对照配对（requires_control=True），"
                          "本阶段不做退化判定",
            })
            continue
        metrics.append(metric)
    return metrics, skipped


def _series_tail(series: Mapping[str, Any]) -> dict[str, Any]:
    """取每条序列的末值（序列为空时留空）。"""
    out: dict[str, Any] = {}
    for key, arr in series.items():
        values = np.asarray(arr)
        out[key] = float(values[-1]) if values.size else None
    return out


def _execute(
    run: RunSpec,
    model: Any,
    metrics: Sequence[Any],
    *,
    store_trajectory: bool,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    """执行一次运行并计算指标。异常向上抛出，由调用方决定如何处理。"""
    record = run_once(model, run.to_request())
    output = record.output

    artifacts: list[str] = []
    if store_trajectory:
        artifacts.append(layout.TRAJECTORY_FILENAME)

    row: dict[str, Any] = {
        "run_id": run.run_id,
        "config_id": run.config_id,
        "model": run.model,
        "mode": run.mode,
        "placement": run.placement,
        "beta_gov": run.beta_gov,
        "tau": run.tau,
        "scenario": run.scenario,
        "seed": run.seed,
        "param_overrides": json.dumps(run.param_overrides, sort_keys=True, default=str),
        "traj_hash": output.traj_hash,
        "network_hash": output.network_hash,
        "wall_seconds": output.wall_seconds,
    }
    for key, value in _series_tail(output.series_ord).items():
        row[f"ord_{key}_final"] = value
    for key, value in _series_tail(output.series_all).items():
        row[f"all_{key}_final"] = value
    for key, value in (output.cost or {}).items():
        row[f"cost_{key}"] = value

    skipped: list[dict[str, str]] = []
    metric_values: dict[str, Any] = {}
    if metrics and not output.params:
        for metric in metrics:
            skipped.append({
                "name": metric.name,
                "reason": "模型未提供 params，指标口径所需的阈值未知",
            })
    else:
        ctx = MetricContext(
            traj=output.traj,
            is_adv=output.is_adv,
            is_gov=output.is_gov,
            params=dict(output.params),
        )
        for metric in metrics:
            values = metric.compute(ctx)
            for key, value in values.items():
                row[f"{metric.name}.{key}"] = value
                metric_values[f"{metric.name}.{key}"] = value

    result = {
        "config_id": run.config_id,
        "traj_hash": output.traj_hash,
        "network_hash": output.network_hash,
        "wall_seconds": output.wall_seconds,
        "series_final_ord": _series_tail(output.series_ord),
        "series_final_all": _series_tail(output.series_all),
        "cost": dict(output.cost or {}),
        "capabilities": dict(record.capabilities),
    }
    return (
        {
            "row": row,
            "result": result,
            "metrics": metric_values,
            "artifacts": artifacts,
            "output": output,
        },
        skipped,
        artifacts,
    )


def run_sweep(
    spec: SweepSpec,
    *,
    out_root: str | Path,
    metrics: Sequence[str] | None = None,
    model: Any = None,
    packages: Sequence[str] = ("numpy",),
    echo: bool = False,
) -> SweepResult:
    """执行一次 sweep。

    参数
    ----
    spec:
        实验规格。
    out_root:
        产物根目录（``<out_root>/runs/<run_id>/``）。
    metrics:
        要计算的指标名；``None`` 表示不计算，``["all"]`` 表示所有不需要
        对照的已注册指标。
    model:
        显式传入的模型；缺省时按 ``spec.model`` 从注册表取。
    """
    root = Path(out_root)
    runs = expand_sweep(spec)

    if model is None:
        model = DYNAMICS.get(spec.model)

    metric_objs, skipped_metrics = resolve_metrics(metrics)
    env = snapshot(packages=packages, extra={"odin_sim_version": __version__})

    result = SweepResult(
        name=spec.name,
        out_root=root,
        fingerprint=spec.fingerprint,
        total=len(runs),
    )

    for index, run in enumerate(runs, start=1):
        rd = layout.run_dir(root, run.run_id)
        existing = read_manifest(rd)

        if existing is not None and existing.get("status") == STATUS_OK:
            if existing.get("spec") == run.to_dict():
                artifacts_ok = True
                if spec.store_trajectory:
                    artifacts_ok = layout.trajectory_path(rd).is_file()
                if artifacts_ok:
                    result.skipped += 1
                    result.rows.append(_row_from_manifest(existing))
                    if echo:
                        print(f"[sweep] {index}/{result.total} skip {run.run_id}")
                    continue

        try:
            payload, skipped, artifacts = _execute(
                run, model, metric_objs, store_trajectory=spec.store_trajectory
            )
        except Exception as exc:
            result.failed += 1
            write_manifest(
                rd,
                run_id=run.run_id,
                spec=run.to_dict(),
                model={"name": getattr(model, "name", spec.model), "capabilities": {}},
                result={},
                environment=env,
                status=STATUS_ERROR,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            if echo:
                print(f"[sweep] {index}/{result.total} FAIL {run.run_id}: {exc}")
            if spec.on_error == "abort":
                result.aborted = True
                _write_summary(root, spec, result, env, skipped_metrics)
                raise SweepError(
                    f"sweep 在第 {index}/{result.total} 条运行时失败并中止："
                    f"run_id={run.run_id} {type(exc).__name__}: {exc}"
                ) from exc
            continue

        output = payload["output"]
        if spec.store_trajectory:
            rd.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                layout.trajectory_path(rd),
                traj=output.traj,
                is_adv=output.is_adv,
                is_gov=output.is_gov,
            )

        all_skipped = list(skipped_metrics) + skipped
        write_manifest(
            rd,
            run_id=run.run_id,
            spec=run.to_dict(),
            model={
                "name": getattr(model, "name", spec.model),
                "capabilities": payload["result"]["capabilities"],
            },
            result=payload["result"],
            environment=env,
            metrics=payload["metrics"],
            skipped_metrics=all_skipped,
            artifacts=payload["artifacts"],
            status=STATUS_OK,
        )
        row = dict(payload["row"])
        row["status"] = STATUS_OK
        result.rows.append(row)
        result.executed += 1
        if echo:
            print(f"[sweep] {index}/{result.total} run  {run.run_id} "
                  f"traj={output.traj_hash}")

    _write_summary(root, spec, result, env, skipped_metrics)
    return result


def _row_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """从已完成的 manifest 还原汇总行（不重跑）。"""
    spec = dict(manifest.get("spec") or {})
    res = dict(manifest.get("result") or {})
    row: dict[str, Any] = {
        "run_id": manifest.get("run_id", ""),
        "config_id": res.get("config_id", ""),
        "model": spec.get("model", ""),
        "mode": spec.get("mode", ""),
        "placement": spec.get("placement", ""),
        "beta_gov": spec.get("beta_gov", ""),
        "tau": spec.get("tau", ""),
        "scenario": spec.get("scenario", ""),
        "seed": spec.get("seed", ""),
        "param_overrides": json.dumps(
            spec.get("param_overrides") or {}, sort_keys=True, default=str
        ),
        "traj_hash": res.get("traj_hash", ""),
        "network_hash": res.get("network_hash", ""),
        "wall_seconds": res.get("wall_seconds", ""),
        "status": "skipped",
    }
    for key, value in (res.get("series_final_ord") or {}).items():
        row[f"ord_{key}_final"] = value
    for key, value in (res.get("series_final_all") or {}).items():
        row[f"all_{key}_final"] = value
    for key, value in (res.get("cost") or {}).items():
        row[f"cost_{key}"] = value
    for key, value in (manifest.get("metrics") or {}).items():
        row[key] = value
    return row


def _write_summary(
    root: Path,
    spec: SweepSpec,
    result: SweepResult,
    env: Mapping[str, Any],
    skipped_metrics: Sequence[Mapping[str, str]],
) -> None:
    result.results_csv = write_results_csv(root, result.rows)
    result.sweep_manifest = write_sweep_manifest(
        root,
        {
            "manifest_version": "odin-sim-sweep-v1",
            "name": spec.name,
            "sweep_fingerprint": spec.fingerprint,
            "spec": spec.to_dict(),
            "counts": {
                "total": result.total,
                "executed": result.executed,
                "skipped": result.skipped,
                "failed": result.failed,
            },
            "aborted": result.aborted,
            "skipped_metrics": [dict(m) for m in skipped_metrics],
            "environment": env,
        },
    )
