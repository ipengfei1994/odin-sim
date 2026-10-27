"""模型层协议与注册表测试（不依赖 baseline）。

覆盖
----
* 注册表：注册 / 查得 / 重复注册报错 / 未注册报错信息
* 协议：G3 适配器类在结构上满足协议（不需要 baseline 即可断言）
* 能力声明：G3 明确声明不支持逐步驱动，引擎不得假设
"""
from __future__ import annotations

import pytest

from odin_sim.model import (
    DYNAMICS,
    METRICS,
    TOPOLOGY,
    Registry,
    RegistryError,
    builtin_registration_error,
)
from odin_sim.model.adapters.g3 import (
    G3AlertLead,
    G3Backfire,
    G3Dynamics,
    G3ExtremeFinal,
    G3JointFinal,
    G3Topology,
)
from odin_sim.model.protocol import (
    DynamicsModel,
    Metric,
    ModelCapabilities,
    RunRequest,
    TopologyModel,
)


def test_registry_register_and_get():
    reg: Registry[str] = Registry("测试")
    reg.register("a", "A")
    assert reg.get("a") == "A"
    assert reg.has("a") and not reg.has("b")
    assert reg.names() == ["a"]


def test_registry_duplicate_registration_rejected():
    reg: Registry[str] = Registry("测试")
    reg.register("a", "A")
    with pytest.raises(RegistryError, match="已注册同名条目"):
        reg.register("a", "A2")
    # 显式覆盖是允许的，但要写进代码里，防止静默替换
    reg.register("a", "A2", overwrite=True)
    assert reg.get("a") == "A2"


def test_registry_missing_name_reports_available():
    reg: Registry[str] = Registry("测试")
    reg.register("a", "A")
    with pytest.raises(RegistryError, match="未注册"):
        reg.get("zzz")


def test_g3_classes_satisfy_protocols():
    # runtime_checkable 只做结构检查（属性 + 方法存在），不需要真实运行
    assert isinstance(G3Dynamics(), DynamicsModel)
    assert isinstance(G3Topology(), TopologyModel)
    assert isinstance(G3ExtremeFinal(), Metric)
    assert isinstance(G3JointFinal(), Metric)
    assert isinstance(G3Backfire(), Metric)
    assert isinstance(G3AlertLead(), Metric)


def test_g3_dynamics_declares_no_stepwise_support():
    caps = G3Dynamics.capabilities
    assert caps.stepwise is False, (
        "G3 baseline 内核不支持逐步驱动；声明为 True 会让引擎走上不存在的代码路径"
    )
    assert caps.multi_dim is True
    assert caps.requires_control is True


def test_g3_backfire_requires_control_flag():
    assert G3Backfire.requires_control is True
    assert G3ExtremeFinal.requires_control is False


def test_run_request_config_id_matches_baseline_convention():
    req = RunRequest(mode="cross", placement="hub", beta_gov=0.20, tau=60, seed=7)
    assert req.config_id == "cross_hub_b200_t60"


def test_run_request_defaults_are_explicit():
    req = RunRequest(mode="none", seed=1)
    assert req.placement == "ba"
    assert req.beta_gov == 0.0
    assert req.tau == 0
    assert req.scenario == "polarized"
    assert req.param_overrides == {}


def test_capabilities_defaults_do_not_overclaim():
    caps = ModelCapabilities()
    assert caps.stepwise is False
    assert caps.multi_dim is False
    assert caps.requires_control is False


def test_builtin_registration_is_all_or_nothing():
    """内建注册要么全成功，要么全不注册——不允许"注册一半"。

    半注册会让调用方以为模型可用，是实验静默跑错实现的典型来源。
    """
    err = builtin_registration_error()
    if err is None:
        assert "g3-baseline-v1" in DYNAMICS.names()
        assert "g3-baseline-v1" in TOPOLOGY.names()
        assert "g3.extreme_final_ord" in METRICS.names()
        assert "g3.backfire" in METRICS.names()
    else:
        assert isinstance(err, str) and err
        assert DYNAMICS.names() == []
        assert TOPOLOGY.names() == []
        assert METRICS.names() == []
