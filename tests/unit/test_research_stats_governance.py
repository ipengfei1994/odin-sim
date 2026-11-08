"""S5 验收：统计检验与实验形态展开。"""
from __future__ import annotations

import pytest

from odin_sim.research.config import ConfigError, ExperimentConfig
from odin_sim.research.governance import (
    DEFAULT_CONTROL_GROUPS, DIRECTION_FORWARD, DIRECTION_REVERSE,
    expand_control_groups, expand_research_runs, finite_size_series,
    hysteresis_pairs, run_fingerprint,
)
from odin_sim.research.stats import (
    STATISTICS_VERSION, StatsError, benjamini_hochberg, bf01_bic_approx,
    cohens_d, compare_groups, mann_whitney_u,
)


def base(**overrides):
    data = {
        "name": "t", "model": "synthetic", "population_size": 10,
        "seeds": [1, 2], "simulation_steps": 2,
        "community_partition_method": "explicit_label",
    }
    data.update(overrides)
    return data


# --- 统计 ------------------------------------------------------------------
def test_mann_whitney_known_input():
    r = mann_whitney_u([1, 2, 3], [4, 5, 6])
    assert r["u1"] == 0 and r["u2"] == 9 and r["u"] == 0
    assert r["n1"] == 3 and r["n2"] == 3
    assert 0.0 <= r["p_normal_approx"] <= 1.0


def test_mann_whitney_tie_correction():
    r = mann_whitney_u([1, 1], [2, 2])
    assert r["u1"] == 0
    assert r["tie_corrected"] == 1.0
    assert r["p_normal_approx"] >= 0


def test_cohens_d_known_input():
    assert cohens_d([1, 2, 3], [4, 5, 6]) == pytest.approx(-3.0)
    with pytest.raises(StatsError, match="不能为空"):
        cohens_d([], [1])


def test_benjamini_hochberg_known_input():
    r = benjamini_hochberg([0.01, 0.02, 0.03], alpha=0.05)
    assert r["adjusted_pvalues"] == pytest.approx([0.03, 0.03, 0.03])
    assert r["rejected"] == [True, True, True]
    assert r["n_tests"] == 3
    with pytest.raises(StatsError, match="alpha"):
        benjamini_hochberg([0.5], alpha=0.0)


def test_bf01_identical_groups_is_one():
    assert bf01_bic_approx([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_compare_groups_has_no_pass_fail_and_is_marked_unlocked():
    r = compare_groups([1, 2, 3], [4, 5, 6])
    assert r["statistics_version"] == STATISTICS_VERSION == "unlocked"
    assert r["thresholds_locked"] is False
    assert "pass" not in r and "failed" not in r
    assert "BIC 近似" in r["note"]
    assert "mann_whitney_u" in r and "cohens_d" in r and "bh_fdr" in r


# --- 实验形态 --------------------------------------------------------------
def test_deterministic_expansion_and_run_keys():
    cfg = ExperimentConfig.from_dict(base(
        population_sizes=[10, 20], seeds=[1, 2],
        backends=["backend-a", "backend-b"],
        control_groups=list(DEFAULT_CONTROL_GROUPS), hysteresis_scan=True,
    ))
    a = expand_research_runs(cfg)
    b = expand_research_runs(cfg)
    assert [r.run_key for r in a] == [r.run_key for r in b]
    assert len(a) == 2 * 2 * 2 * 3 * 2
    assert len({r.run_key for r in a}) == len(a)
    assert len({run_fingerprint(r) for r in a}) == len(a)


def test_control_groups_and_hysteresis_pairs():
    cfg = ExperimentConfig.from_dict(base(
        population_sizes=[10], seeds=[1],
        control_groups=["no_intervention", "random_script"],
        hysteresis_scan=True,
    ))
    assert expand_control_groups(cfg) == ["main", "no_intervention", "random_script"]
    runs = expand_research_runs(cfg)
    pairs = hysteresis_pairs(runs)
    assert len(pairs) == 3
    assert all(f.direction == DIRECTION_FORWARD and r.direction == DIRECTION_REVERSE
               for f, r in pairs)
    with pytest.raises(ValueError, match="未知对照组"):
        expand_control_groups(ExperimentConfig.from_dict(
            base(control_groups=["made_up"])))


def test_finite_size_series_and_default_dimensions():
    cfg = ExperimentConfig.from_dict(base(population_sizes=[10, 20, 40], seeds=[1]))
    series = finite_size_series(expand_research_runs(cfg))
    assert list(series) == [10, 20, 40]
    assert all(len(v) == 1 for v in series.values())
    assert len(expand_research_runs(ExperimentConfig.from_dict(base()))) == 2


def test_duplicate_seeds_rejected_not_deduplicated():
    with pytest.raises(ConfigError, match="重复"):
        ExperimentConfig.from_dict(base(seeds=[1, 1]))
