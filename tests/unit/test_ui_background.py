"""P2-3 测试：后台异步编排（纯函数 + configure 页流程冒烟）。"""
from __future__ import annotations

import json
from pathlib import Path

from odin_sim.ui import background


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def test_sweep_command_builds_cli_argv():
    import sys

    cmd = background.sweep_command("cfg.json", "out/", metrics=["a", "b"])
    assert cmd[0] == sys.executable
    assert cmd[3] == "sweep"
    assert cmd[cmd.index("--config") + 1] == "cfg.json"
    assert cmd[cmd.index("--out") + 1] == "out/"
    assert cmd[cmd.index("--metrics") + 1] == "a,b"


def test_write_config_writes_sweep_config_json(tmp_path):
    cfg = {"name": "x", "model": "m"}
    path = background.write_config(cfg, tmp_path / "run1")
    assert path.name == "sweep_config.json"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "x"


def test_progress_count_counts_manifests(tmp_path):
    runs = tmp_path / "runs"
    for i in range(3):
        d = runs / f"run{i}"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}", encoding="utf-8")
    assert background.progress_count(tmp_path) == 3
    assert background.progress_count(tmp_path / "empty") == 0


def test_read_log_tail_truncates_long(tmp_path):
    log = tmp_path / background.LOG_FILENAME
    log.write_text("A" * 5000, encoding="utf-8")
    tail = background.read_log_tail(tmp_path, max_chars=10)
    assert "截断" in tail and len(tail) <= 30


class _FakeProc:
    def __init__(self, final_code: int = 0, running_calls: int = 1):
        self._final = final_code
        self._running = running_calls
        self._n = 0
        self.terminated = False

    def poll(self):
        self._n += 1
        if self._n <= self._running:
            return None
        return self._final

    def terminate(self):
        self.terminated = True

    def force_done(self):
        self._running = -1


def test_task_status_running(tmp_path):
    proc = _FakeProc(final_code=0, running_calls=5)
    st = background.task_status(proc, tmp_path, total=10)
    assert st["state"] == "running"
    assert st["done"] == 0 and st["total"] == 10 and st["error"] is None


def test_task_status_done(tmp_path):
    proc = _FakeProc(final_code=0, running_calls=0)
    st = background.task_status(proc, tmp_path, total=10)
    assert st["state"] == "done"


def test_task_status_failed_reads_log(tmp_path):
    (tmp_path / background.LOG_FILENAME).write_text(
        "[error] boom", encoding="utf-8"
    )
    proc = _FakeProc(final_code=1, running_calls=0)
    st = background.task_status(proc, tmp_path, total=10)
    assert st["state"] == "failed"
    assert "boom" in st["error"]


# --------------------------------------------------------------------------- #
# configure 页后台流程（用 fake 进程，避内核）
# --------------------------------------------------------------------------- #
def _btn(at, key: str):
    return next(b for b in at.button if b.key == key)


def test_configure_background_flow_running_then_done(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    fake = _FakeProc(final_code=0, running_calls=99)  # 一直 running，手工推进
    monkeypatch.setattr(background, "launch", lambda *a, **k: fake)
    import odin_sim.ui.pages.configure as cfg_page

    # 本机可能无 odin_g3，绕过模型注册检查以便走完后台分支
    monkeypatch.setattr(cfg_page.DYNAMICS, "has", lambda name: True)

    script = tmp_path / "app.py"
    script.write_text(
        "from odin_sim.ui.pages.configure import render\nrender()\n",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(script))
    at.run(timeout=30)
    assert len(at.exception) == 0

    at.radio[0].set_value("后台（推荐大网格）")
    _btn(at, "run_sweep").click()
    at.run(timeout=30)
    # 进入"运行中"监控（手动刷新按钮，无自动 rerun 循环）
    assert "ui_async_task" in at.session_state
    assert len(at.info) > 0

    # 手动推进到完成，再次渲染
    fake.force_done()
    at.run(timeout=30)
    assert len(at.exception) == 0
    joined = " ".join(str(e.value) for e in at.success)
    assert "完成" in joined
    # 完成后应把目录交给浏览结果页
    assert "pending_browse_dir" in at.session_state


def test_configure_background_clears_stale_process(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    fake = _FakeProc(final_code=0, running_calls=99)
    monkeypatch.setattr(background, "launch", lambda *a, **k: fake)
    import odin_sim.ui.pages.configure as cfg_page

    monkeypatch.setattr(cfg_page.DYNAMICS, "has", lambda name: True)

    script = tmp_path / "app.py"
    script.write_text(
        "from odin_sim.ui.pages.configure import render\nrender()\n",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(script))
    at.run(timeout=30)
    at.radio[0].set_value("后台（推荐大网格）")
    _btn(at, "run_sweep").click()
    at.run(timeout=30)
    assert fake.terminated is False  # 启动后还在跑
    # 再次点击运行会先 _clear_task → terminate 上一个
    _btn(at, "run_sweep").click()
    at.run(timeout=30)
    assert fake.terminated is True
