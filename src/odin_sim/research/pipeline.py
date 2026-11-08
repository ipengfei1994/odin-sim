"""研究实验运行器：把 S1~S5 串成一条可复现、可审计的执行链。

执行纪律
--------
1. **不伪造事件**：模型未声明 stepwise 能力时（如既有 G3 baseline），只写
   ``event_level_unavailable`` 的原因，不生成任何事件、不写空 events.jsonl。
2. **不伪造指标**：``DefinitionVersion.metric_def_unset`` 时全部 75 条进
   ``skipped_metrics``，只落盘原始观测量（``observables.json``）。
3. **过程量实时落盘**：事件由 ``EventRecorder`` 在发生时立即写入；记录、网络、
   指标在 run 结束时由已落盘数据派生，不重复保存可可靠推导的中间量。
4. **复现可验证**：事件流 SHA-256 与 ``events.jsonl`` 一同落盘，重放逐字符比对。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import ExperimentConfig
from .events import EventType, MetricRecorded, make_event
from .governance import expand_research_runs
from .metrics import METRIC_IDS, MetricInput, MetricOutcome, compute_all, observables
from .networks import NetworkStore
from .recorder import EventRecorder, read_events, replay_digest
from .records import ExperimentMetadata, SeparationGuard
from .stats import STATISTICS_VERSION
from .stepwise import StepwiseError, run_stepwise, supports_stepwise
from .synthetic import SyntheticPopulation

__all__ = ["ResearchResult", "run_research_experiment", "verify_run_replay"]

CONFIG_FILENAME = "experiment_config.json"
RECORDS_FILENAME = "records.json"
OBSERVABLES_FILENAME = "observables.json"
METRICS_FILENAME = "metrics.json"
RUN_MANIFEST_FILENAME = "experiment_manifest.json"
SWEEP_MANIFEST_FILENAME = "research_manifest.json"


@dataclass
class ResearchResult:
    out_root: Path
    experiment_id: str
    total_runs: int = 0
    executed: int = 0
    event_level_unavailable: int = 0
    failed: int = 0
    total_events: int = 0
    computed_metrics: int = 0
    skipped_metrics: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    run_dirs: list[Path] = field(default_factory=list)
    manifest_path: Path | None = None
    separation_errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not any(self.separation_errors.values())


def _json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )


def _records_payload(records: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    return {
        key: [asdict(r) for r in values]
        for key, values in records.items()
    }


def run_research_experiment(
    config: ExperimentConfig,
    *,
    out_root: str | Path,
    model: Any = None,
    registry: Any = None,
    echo: bool = False,
) -> ResearchResult:
    """执行一组研究实验。返回可审计的结果对象。"""
    config.validate()
    root = Path(out_root)
    runs = expand_research_runs(config)
    base_fingerprint = config.fingerprint
    experiment_id = f"exp-{base_fingerprint}"

    result = ResearchResult(out_root=root, experiment_id=experiment_id, total_runs=len(runs))
    _json(root / CONFIG_FILENAME, config.to_dict())

    for run in runs:
        rd = root / "runs" / run.run_key
        rd.mkdir(parents=True, exist_ok=True)
        result.run_dirs.append(rd)

        # 对照组口径由配置派生，但不改变实验级 fingerprint。
        group_cfg = _config_for_group(config, run.control_group)
        stepwise_model = model
        if stepwise_model is None:
            if config.model == "synthetic":
                stepwise_model = SyntheticPopulation(
                    population_size=run.population_size, seed=run.seed
                )
            else:
                stepwise_model = None

        manifest: dict[str, Any] = {
            "manifest_version": "odin-research-run-v1",
            "run_key": run.run_key,
            "experiment_id": experiment_id,
            "simulation_id": run.simulation_id,
            "population_size": run.population_size,
            "seed": run.seed,
            "backend": run.backend,
            "control_group": run.control_group,
            "direction": run.direction,
            "config_version": config.config_version,
            "definition_version": config.definition_version.to_dict(),
            "reproducibility_level": config.reproducibility_level,
            "statistics_version": STATISTICS_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        if stepwise_model is None or not supports_stepwise(stepwise_model):
            # 整段轨迹型模型：事件级数据不可得，绝不伪造。
            result.event_level_unavailable += 1
            model_name = config.model if stepwise_model is None else type(stepwise_model).__name__
            reason = (
                f"模型 {model_name} 未声明 stepwise 能力（整段轨迹型），"
                "ContentExposed / ExposureSelected / OpinionChanged 等事件事后无法重建；"
                "本次不产出事件级数据，也不计算依赖这些数据的指标"
            )
            outcomes = [
                MetricOutcome(mid, "skipped", reason=reason,
                              definition_version=config.definition_version.metric_def_version)
                for mid in METRIC_IDS
            ]
            manifest.update({
                "event_level_unavailable": True,
                "reason": reason,
                "artifacts": [METRICS_FILENAME],
                "n_events": 0,
            })
            _json(rd / METRICS_FILENAME, _outcomes_payload(outcomes))
            _json(rd / RUN_MANIFEST_FILENAME, manifest)
            _accumulate(result, outcomes)
            if echo:
                print(f"[research] {run.run_key} 事件级数据不可得（整段轨迹模型）")
            continue

        try:
            outcomes, n_events, sep_errors = _run_stepwise_once(
                stepwise_model, config, group_cfg, run, rd, registry
            )
            if sep_errors:
                result.separation_errors[run.run_key] = sep_errors
        except (StepwiseError, ValueError, OSError) as exc:
            result.failed += 1
            manifest.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            _json(rd / RUN_MANIFEST_FILENAME, manifest)
            if echo:
                print(f"[research] {run.run_key} FAIL {exc}")
            continue

        result.executed += 1
        result.total_events += n_events
        _accumulate(result, outcomes)
        if echo:
            print(f"[research] {run.run_key} events={n_events} "
                  f"computed={sum(1 for o in outcomes if not o.skipped)}")

    result.manifest_path = _write_sweep_manifest(root, config, result, experiment_id)
    return result


def _config_for_group(config: ExperimentConfig, group: str) -> ExperimentConfig:
    if group == "main":
        return config
    if group == "no_intervention":
        return config.with_overrides(intervention_config={})
    if group == "random_script":
        iv = dict(config.intervention_config)
        iv["gov_kappa"] = "random_script"
        return config.with_overrides(intervention_config=iv)
    return config


def _run_stepwise_once(
    stepwise_model: Any,
    base_config: ExperimentConfig,
    run_config: ExperimentConfig,
    run: Any,
    rd: Path,
    registry: Any,
) -> tuple[list[MetricOutcome], int, list[str]]:
    """一条运行的逐步执行、落盘、分离校验与指标门控。"""
    recorder = EventRecorder(rd / "events.jsonl")
    try:
        step = run_stepwise(
            stepwise_model,
            config=run_config,
            simulation_id=run.simulation_id,
            experiment_id=run.experiment_id,
            recorder=recorder,
        )
        records, graphs = step.records, step.graphs

        inp = MetricInput(config=run_config, records=records, graphs=graphs)
        outcomes = compute_all(inp, registry=registry)

        # 把 75 条结论作为 phase-9 事件写入同一个事件流（可审计的 skipped 记录）。
        for outcome in outcomes:
            value = outcome.value if isinstance(outcome.value, float) else None
            status = "skipped" if outcome.skipped else "recorded"
            reason = outcome.reason or (
                "复合值见 metrics.json" if isinstance(outcome.value, dict) else ""
            )
            payload = MetricRecorded(
                metric_id=outcome.metric_id, value=value, status=status,
                reason=reason,
                definition_version=outcome.definition_version,
            )
            recorder.append(make_event(
                EventType.METRIC_RECORDED,
                simulation_id=run.simulation_id,
                experiment_id=run.experiment_id,
                tick=max(step.n_ticks - 1, 0),
                sequence_no=recorder.count,
                payload=payload,
            ))
        digest = recorder.close()
        recorder.write_digest()
    except Exception:
        recorder.close()
        raise

    # --- 三条分离的字段级验收 -----------------------------------------
    errors = SeparationGuard.check_all(
        snapshots=records.get("agent_snapshots") or (),
        decisions=records.get("decisions") or (),
        interactions=records.get("interactions") or (),
        networks=records.get("network_snapshots") or (),
        influences=records.get("influences") or (),
    )

    # --- 产物 ---------------------------------------------------------
    NetworkStore.save(graphs, rd)
    metadata = ExperimentMetadata(
        experiment_id=run.experiment_id, simulation_id=run.simulation_id,
        config_fingerprint=run_config.fingerprint,
        config_version=run_config.config_version,
        definition_version=run_config.definition_version.to_dict(),
        reproducibility_level=run_config.reproducibility_level,
        model_metadata=dict(run_config.model_metadata),
        recommendation_policy=dict(run_config.recommendation_policy),
        intervention_config=dict(run_config.intervention_config),
        initial_opinion_distribution=dict(run_config.initial_opinion_distribution),
        opinion_dynamics_config=dict(run_config.opinion_dynamics_config),
        population_size=run.population_size,
    )
    all_records = dict(records)
    all_records["experiment_metadata"] = [metadata]
    _json(rd / RECORDS_FILENAME, _records_payload(all_records))
    _json(rd / OBSERVABLES_FILENAME, observables(records, graphs, config=run_config))
    _json(rd / METRICS_FILENAME, _outcomes_payload(outcomes))

    event_count = recorder.count
    manifest = {
        "manifest_version": "odin-research-run-v1",
        "run_key": run.run_key,
        "experiment_id": run.experiment_id,
        "simulation_id": run.simulation_id,
        "population_size": run.population_size,
        "seed": run.seed,
        "backend": run.backend,
        "control_group": run.control_group,
        "direction": run.direction,
        "config_version": run_config.config_version,
        "config_fingerprint": run_config.fingerprint,
        "definition_version": run_config.definition_version.to_dict(),
        "reproducibility_level": run_config.reproducibility_level,
        "statistics_version": STATISTICS_VERSION,
        "status": "ok" if not errors else "separation_error",
        "n_ticks": step.n_ticks,
        "n_events": event_count,
        "event_digest": digest,
        "separation_errors": errors,
        "s0_completed": run_config.definition_version.definitions_locked,
        "artifacts": [
            "events.jsonl", "events.sha256", "records.json", "observables.json",
            "metrics.json", "network_potential.json", "network_exposure.json",
            "network_influence.json", "network_interaction.json",
        ],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _json(rd / RUN_MANIFEST_FILENAME, manifest)
    if errors:
        # 不吞掉分离违规：写进结果对象，让 ok=False 显式暴露。
        return outcomes, event_count, errors
    return outcomes, event_count, []


def _outcomes_payload(outcomes: Sequence[MetricOutcome]) -> dict[str, Any]:
    computed = [o for o in outcomes if not o.skipped]
    skipped = [o for o in outcomes if o.skipped]
    return {
        "n_total": len(outcomes),
        "n_computed": len(computed),
        "n_skipped": len(skipped),
        "thresholds_locked": False,
        "note": "阈值未锁定：本文件不含任何 pass/fail 达标判定",
        "computed": [o.to_dict() for o in computed],
        "skipped_metrics": [o.to_dict() for o in skipped],
    }


def _accumulate(result: ResearchResult, outcomes: Sequence[MetricOutcome]) -> None:
    for outcome in outcomes:
        if outcome.skipped:
            result.skipped_metrics += 1
            key = outcome.reason.split("：")[0][:80]
            result.skipped_reasons[key] = result.skipped_reasons.get(key, 0) + 1
        else:
            result.computed_metrics += 1


def _write_sweep_manifest(
    root: Path, config: ExperimentConfig, result: ResearchResult, experiment_id: str
) -> Path:
    path = root / SWEEP_MANIFEST_FILENAME
    reasons = dict(sorted(result.skipped_reasons.items(), key=lambda kv: (-kv[1], kv[0])))
    _json(path, {
        "manifest_version": "odin-research-sweep-v1",
        "experiment_id": experiment_id,
        "config_fingerprint": config.fingerprint,
        "config_version": config.config_version,
        "definition_version": config.definition_version.to_dict(),
        "reproducibility_level": config.reproducibility_level,
        "statistics_version": STATISTICS_VERSION,
        "s0_completed": config.definition_version.definitions_locked,
        "counts": {
            "total_runs": result.total_runs,
            "executed": result.executed,
            "event_level_unavailable": result.event_level_unavailable,
            "failed": result.failed,
            "total_events": result.total_events,
            "computed_metrics": result.computed_metrics,
            "skipped_metrics": result.skipped_metrics,
        },
        "skipped_reason_counts": reasons,
        "open_questions_blocking": 27,
        "note": (
            "S0 未完成：43 条数学定义仍为 TBD，27 项 Open Questions 为阻塞级；"
            "本次只验证数据通道，未产出任何 v2 指标数值"
        ),
    })
    return path


def verify_run_replay(run_dir: str | Path) -> dict[str, Any]:
    """重放事件流并比对落盘哈希（T-REPRO/2：逐字符，不允许近似相等）。"""
    rd = Path(run_dir)
    events = read_events(rd / "events.jsonl")
    digest_path = rd / "events.sha256"
    if not digest_path.is_file():
        return {"ok": False, "reason": "缺少 events.sha256"}
    expected = digest_path.read_text(encoding="utf-8").strip()
    # 落盘哈希包含全部事件（含 phase-9 指标事件），因此直接比对全文重放。
    actual = replay_digest(events)
    return {
        "ok": actual == expected,
        "expected": expected,
        "actual": actual,
        "n_events": len(events),
    }
