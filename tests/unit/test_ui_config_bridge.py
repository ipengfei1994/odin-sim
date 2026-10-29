"""config_bridge 单测：验证 UI 配置桥与 CLI 校验口径一致。

不依赖 [ui] 运行时（不 import streamlit），也不加载 baseline。
"""
from odin_sim.ui.config_bridge import (
    sweep_spec_from_json,
    sweep_spec_to_json,
    validate_sweep_config,
)

GOOD_CONFIG = {
    "name": "ui_bridge_demo",
    "model": "g3-baseline-v1",
    "base": {
        "mode": "cross",
        "placement": "hub",
        "beta_gov": 0.2,
        "tau": 60,
        "scenario": "polarized",
    },
    "axes": {"beta_gov": [0.0, 0.2]},
    "seeds": {"start": 1, "count": 2, "step": 1},
    "store_trajectory": True,
}


def test_good_config_validates_and_counts_runs():
    v = validate_sweep_config(GOOD_CONFIG)
    assert v.ok is True
    assert v.spec is not None
    assert v.error is None
    # 2 个轴值 × 2 个种子 = 4 条 run
    assert v.run_count == 4


def test_missing_required_field_is_rejected_with_readable_error():
    bad = dict(GOOD_CONFIG)
    bad["base"] = {
        "placement": "hub",
        "beta_gov": 0.2,
        "tau": 60,
        "scenario": "polarized",
    }  # 缺必填的 mode
    v = validate_sweep_config(bad)
    assert v.ok is False
    assert v.spec is None
    assert v.run_count is None
    assert v.error  # 非空可读信息，便于 UI 直接展示


def test_unknown_field_is_rejected():
    bad = dict(GOOD_CONFIG)
    bad["extra_field"] = "should fail"
    v = validate_sweep_config(bad)
    assert v.ok is False
    assert v.error


def test_json_roundtrip_preserves_spec():
    v = validate_sweep_config(GOOD_CONFIG)
    text = sweep_spec_to_json(v.spec)
    spec2 = sweep_spec_from_json(text)
    assert spec2.to_dict() == v.spec.to_dict()
