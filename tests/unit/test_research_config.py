"""S1 与核心隔离验收：配置规范 + 既有 G3 数值路径不受影响。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from odin_sim.experiments.spec import RunSpec, SweepSpec
from odin_sim.research.config import (
    CONFIG_VERSION, DEFINITION_UNSET, ConfigError, DefinitionVersion,
    ExperimentConfig,
)
from odin_sim.storage.manifest import write_manifest


def base(**overrides):
    data = {
        "name": "test", "model": "synthetic",
        "population_size": 12, "seeds": [1, 2],
        "simulation_steps": 3,
        "community_partition_method": "explicit_label",
    }
    data.update(overrides)
    return data


def test_partition_method_is_mandatory():
    data = base()
    del data["community_partition_method"]
    with pytest.raises(ConfigError, match="community_partition_method 必填"):
        ExperimentConfig.from_dict(data)


def test_unknown_partition_method_rejected():
    with pytest.raises(ConfigError, match="非法"):
        ExperimentConfig.from_dict(base(community_partition_method="guess"))


def test_q31_bare_symbols_rejected():
    for section, key in (
        ("intervention_config", "tau"),
        ("recommendation_policy", "alpha"),
        ("opinion_dynamics_config", "kappa"),
    ):
        with pytest.raises(ConfigError, match="Q-31"):
            ExperimentConfig.from_dict(base(**{section: {key: 0.5}}))
    # 带前缀是合法的
    cfg = ExperimentConfig.from_dict(base(
        intervention_config={"gov_tau": "hub", "gov_kappa": "fact_check", "gov_omega": 2},
        recommendation_policy={"rec_alpha": 0.3},
    ))
    assert cfg.intervention_config["gov_tau"] == "hub"


def test_unknown_top_level_field_rejected():
    with pytest.raises(ConfigError, match="未知字段"):
        ExperimentConfig.from_dict(base(unexpected=1))


def test_definition_unset_by_default_and_all_thresholds_unlocked():
    cfg = ExperimentConfig.from_dict(base())
    assert cfg.definition_version.metric_def_version == DEFINITION_UNSET
    assert cfg.definition_version.definitions_locked is False
    assert cfg.definition_version.thresholds_locked is False
    assert cfg.config_version == CONFIG_VERSION


def test_fingerprint_stable_and_order_independent():
    a = ExperimentConfig.from_dict(base(model_metadata={"x": 1, "y": 2}))
    b = ExperimentConfig.from_dict(base(model_metadata={"y": 2, "x": 1}))
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint) == 16
    assert a.with_overrides(simulation_steps=4).fingerprint != a.fingerprint


def test_population_sizes_and_repro_levels():
    cfg = ExperimentConfig.from_dict(base(population_sizes=[10, 20, 40]))
    assert cfg.effective_population_sizes == (10, 20, 40)
    with pytest.raises(ConfigError, match="reproducibility_level"):
        ExperimentConfig.from_dict(base(reproducibility_level="maybe"))


def test_to_core_sweep_does_not_carry_research_axes():
    """研究维度不得塞进核心 SweepSpec，否则既有 sweep 指纹会变。"""
    cfg = ExperimentConfig.from_dict(base(
        population_sizes=[10, 20], control_groups=["main", "no_intervention"],
        backends=["a", "b"], hysteresis_scan=True,
    ))
    sweep: SweepSpec = cfg.to_core_sweep()
    assert "population_sizes" not in sweep.to_dict()
    assert "control_groups" not in sweep.to_dict()
    assert sweep.to_dict()["seeds"] == [1, 2]


def test_run_spec_to_dict_contract_unchanged():
    """G3 指纹回归的生命线：键集与取值不得被研究层改动。"""
    run = RunSpec(model="g3-baseline-v1", mode="none", seed=7)
    assert set(run.to_dict()) == {
        "model", "mode", "seed", "placement", "beta_gov", "tau",
        "scenario", "param_overrides",
    }
    assert run.to_dict()["param_overrides"] == {}
    assert run.run_id == run.spec_fingerprint
    assert len(run.run_id) == 16


def test_manifest_research_block_is_opt_in(tmp_path: Path):
    kwargs = dict(
        run_id="abc", spec={"model": "g3"}, model={"name": "g3"},
        result={}, environment={},
    )
    p1 = write_manifest(tmp_path / "r1", **kwargs)
    assert "research" not in json.loads(p1.read_text(encoding="utf-8"))
    p2 = write_manifest(tmp_path / "r2", research={"config_version": CONFIG_VERSION}, **kwargs)
    assert json.loads(p2.read_text(encoding="utf-8"))["research"]["config_version"] == CONFIG_VERSION


def test_core_never_imports_research():
    """分层纪律：核心层零反向依赖（api/ 也不得 import ui/）。"""
    root = Path(__file__).resolve().parents[2] / "src" / "odin_sim"
    core_dirs = ("model", "experiments", "engine", "storage", "topology", "analysis")
    violations = []
    for dirname in core_dirs:
        for path in (root / dirname).rglob("*.py"):
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if ("import" in line and "research" in line
                        and ("from ..research" in line or "from ...research" in line
                             or "odin_sim.research" in line)):
                    violations.append(f"{path.name}:{line_no}: {line.strip()}")
    assert not violations, violations


def test_definition_version_can_be_locked_explicitly():
    cfg = ExperimentConfig.from_dict(base(definition_version={
        "metric_def_version": "metric_def_v1",
        "threshold_lock_version": "thr_v1",
        "thresholds_locked": True,
        "source": "researcher-signed-2026-11-08",
    }))
    assert cfg.definition_version.definitions_locked is True
    assert cfg.definition_version.thresholds_locked is True
    assert DefinitionVersion().definitions_locked is False
