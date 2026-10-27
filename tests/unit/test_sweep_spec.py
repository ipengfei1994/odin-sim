"""实验规格层的单元测试（不依赖任何模型与 baseline）。"""
from __future__ import annotations

import pytest

from odin_sim.experiments import (
    RunSpec,
    SpecError,
    SweepSpec,
    expand_sweep,
    sweep_from_json,
)


def _spec(**kw) -> SweepSpec:
    base = {
        "name": "t",
        "model": "fake-v1",
        "base": {"mode": "cross"},
        "axes": {"placement": ["hub", "random"], "beta_gov": [0.0, 0.2]},
        "seeds": [1, 2, 3],
    }
    base.update(kw)
    return SweepSpec.from_dict(base)


def test_expand_count_and_order_are_deterministic():
    spec = _spec()
    runs = expand_sweep(spec)
    assert len(runs) == 2 * 2 * 3

    # 轴在外层、种子在内层：前三条是同一组合的三个种子
    assert [r.seed for r in runs[:3]] == [1, 2, 3]
    assert {r.placement for r in runs[:3]} == {"hub"}
    assert {r.beta_gov for r in runs[:3]} == {0.0}

    again = expand_sweep(spec)
    assert [r.run_id for r in runs] == [r.run_id for r in again]


def test_run_id_is_content_addressed():
    a = RunSpec(model="m", mode="cross", seed=1)
    b = RunSpec(model="m", mode="cross", seed=1)
    c = RunSpec(model="m", mode="cross", seed=2)
    d = RunSpec(model="m", mode="cross", seed=1, param_overrides={"w_gov": 0.5})

    assert a.run_id == b.run_id
    assert a.run_id != c.run_id
    assert a.run_id != d.run_id
    assert len(a.run_id) == 16


def test_run_spec_to_request_roundtrip():
    run = RunSpec(model="m", mode="cross", seed=7, tau=60,
                  param_overrides={"w_gov": 0.35})
    req = run.to_request()
    assert req.mode == "cross" and req.seed == 7 and req.tau == 60
    assert req.param_overrides == {"w_gov": 0.35}
    assert req.config_id == run.config_id


def test_seeds_accept_range_form():
    spec = _spec(seeds={"start": 10, "count": 3, "step": 5})
    assert [r.seed for r in expand_sweep(spec)][:3] == [10, 15, 20]


def test_param_overrides_axis():
    spec = SweepSpec.from_dict({
        "name": "t", "model": "m", "base": {"mode": "cross"},
        "axes": {"param_overrides.w_gov": [0.2, 0.4]}, "seeds": [1],
    })
    runs = expand_sweep(spec)
    assert [r.param_overrides["w_gov"] for r in runs] == [0.2, 0.4]


def test_duplicate_runs_are_rejected_not_deduplicated():
    # 同一字段同时出现在 base 与 axes，且取值有重叠 -> 会展开出重复运行
    spec = SweepSpec.from_dict({
        "name": "t", "model": "m", "base": {"mode": "cross"},
        "axes": {"mode": ["cross"]}, "seeds": [1, 1],
    })
    with pytest.raises(SpecError, match="重复"):
        expand_sweep(spec)


@pytest.mark.parametrize("patch", [
    {"unknown_field": 1},
    {"base": {"placement": "hub"}},          # 缺 mode
    {"seeds": None},
    {"seeds": []},
    {"axes": {"nope": [1]}},
    {"axes": {"placement": []}},
    {"on_error": "maybe"},
    {"base": {"unknown": 1}},
    {"seeds": {"start": 1}},                 # 缺 count
])
def test_invalid_specs_raise(patch):
    data = {
        "name": "t", "model": "m", "base": {"mode": "cross"},
        "axes": {"placement": ["hub"]}, "seeds": [1],
    }
    data.update(patch)
    with pytest.raises(SpecError):
        SweepSpec.from_dict(data)


def test_from_json_and_fingerprint_stable():
    text = (
        '{"name":"t","model":"m","base":{"mode":"cross"},'
        '"axes":{"tau":[0,60]},"seeds":[1,2]}'
    )
    spec = sweep_from_json(text)
    assert spec.fingerprint == sweep_from_json(text).fingerprint
    assert spec.on_error == "abort"
    assert spec.store_trajectory is False


def test_bad_json_raises_spec_error():
    with pytest.raises(SpecError, match="JSON"):
        sweep_from_json("{not json")
