"""P1-4 configure 页测试。

纯函数（build_default_config / parse_config / trigger_sweep 摘要提取）始终可测；
整页渲染用 streamlit 的 AppTest，不点击「运行 sweep」（避免触发真实内核计算）。
"""
from __future__ import annotations

from pathlib import Path

from odin_sim.ui.config_bridge import validate_sweep_config
from odin_sim.ui.pages import configure as configure_page

import pytest

pytest.importorskip("streamlit")


def test_build_default_config_is_valid_and_small():
    cfg = configure_page.build_default_config()
    validation = validate_sweep_config(cfg)
    assert validation.ok, validation.error
    # 默认 2 个 beta_gov × 2 个种子 = 4 条运行，且不超过同步上限
    assert validation.run_count == 4
    assert validation.run_count <= configure_page.MAX_SYNC_RUNS


def test_parse_config_invalid_json_returns_none():
    assert configure_page.parse_config("{not valid") is None


def test_parse_config_non_dict_returns_none():
    assert configure_page.parse_config("[1, 2, 3]") is None
    assert configure_page.parse_config("42") is None


def test_parse_config_valid_roundtrip():
    text = '{"name":"x","model":"g3-baseline-v1","base":{"mode":"cross"},"seeds":[1]}'
    parsed = configure_page.parse_config(text)
    assert parsed is not None
    assert parsed["name"] == "x"


def test_trigger_sweep_extracts_summary(monkeypatch):
    """trigger_sweep 只负责提取轻量摘要，不持有重对象；用假结果验证。"""

    class _FakeResult:
        name = "demo"
        fingerprint = "abc123"
        total = 4
        executed = 4
        skipped = 0
        failed = 0
        aborted = False
        results_csv = Path("/tmp/demo/results.csv")
        sweep_manifest = Path("/tmp/demo/sweep_manifest.json")
        ok = True

    monkeypatch.setattr(configure_page, "run_sweep", lambda *a, **k: _FakeResult())

    summary = configure_page.trigger_sweep(
        configure_page.build_default_config(), "results/_test"
    )
    assert summary["name"] == "demo"
    assert summary["total"] == 4
    assert summary["executed"] == 4
    assert summary["ok"] is True
    assert summary["results_csv"] == str(Path("/tmp/demo/results.csv"))
    assert summary["sweep_manifest"] == str(Path("/tmp/demo/sweep_manifest.json"))
    assert summary["aborted"] is False


def test_render_smoke_shows_valid_config(tmp_path):
    from streamlit.testing.v1 import AppTest

    script = tmp_path / "app.py"
    script.write_text(
        "from odin_sim.ui.pages.configure import render\nrender()\n",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(script))
    at.run()
    assert len(at.exception) == 0
    # 默认配置合法，页面应渲染出成功提示（st.success 而非 st.markdown）
    joined = " ".join(str(e.value) for e in at.success)
    assert "配置合法" in joined


def test_model_availability_message_warns_when_unregistered(monkeypatch):
    monkeypatch.setattr(configure_page.DYNAMICS, "has", lambda name: False)
    monkeypatch.setattr(
        configure_page, "builtin_registration_error", lambda: "odin_g3 缺失"
    )
    msg = configure_page.model_availability_message("g3-baseline-v1")
    assert msg is not None
    assert "未注册" in msg and "odin_g3 缺失" in msg


def test_model_availability_message_none_when_registered(monkeypatch):
    monkeypatch.setattr(configure_page.DYNAMICS, "has", lambda name: True)
    assert configure_page.model_availability_message("g3-baseline-v1") is None


def test_trigger_sweep_propagates_errors_gracefully(monkeypatch):
    """运行中抛出的各类异常（含模型未注册）由页面统一捕获并提示，不崩溃。"""

    def _boom(*a, **k):
        raise RuntimeError("内核不可用")

    monkeypatch.setattr(configure_page, "run_sweep", _boom)
    # trigger_sweep 本身不捕获，交由 render 的 except (SweepError, Exception) 处理；
    # 这里只验证 trigger_sweep 会原样抛出，便于渲染层捕获。
    import pytest

    with pytest.raises(RuntimeError):
        configure_page.trigger_sweep(
            configure_page.build_default_config(), "results/_test"
        )
