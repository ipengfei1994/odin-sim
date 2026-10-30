"""``src/odin_sim/api`` 的契约测试。

覆盖两件事：端点能读出真实产物，以及服务层不重写任何口径。所有期望值都来自
``analysis`` 的同一次调用——若有人在 api 里重算了统计量，这里会先崩。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="需要 [web] 可选依赖")
odin_api = pytest.importorskip("odin_sim.api", reason="需要 [web] 可选依赖")

from fastapi.testclient import TestClient  # noqa: E402

from odin_sim.analysis import aggregate, load_results  # noqa: E402
from odin_sim.model import DYNAMICS  # noqa: E402

RESULTS = Path(__file__).resolve().parents[2] / "results"
DEMO = "ref_demo_dense"


@pytest.fixture(scope="module")
def client():
    app = odin_api.create_app(RESULTS, mount_web=False)
    with TestClient(app) as c:
        yield c


def test_health_reports_root(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert str(RESULTS) in body["results_root"]


def test_models_are_read_from_registry_not_hardcoded(client):
    names = [m["name"] for m in client.get("/api/models").json()["models"]]
    assert names == DYNAMICS.names()


def test_info_is_live_environment_snapshot(client):
    body = client.get("/api/info").json()
    assert "python_version" in body
    assert body["extra"]["odin_sim_version"]


def test_sweeps_list_only_real_directories(client):
    ids = {s["id"] for s in client.get("/api/sweeps").json()["sweeps"]}
    on_disk = {p.name for p in RESULTS.iterdir() if p.is_dir() and (p / "results.csv").is_file()}
    assert on_disk.issubset(ids)


def test_unknown_sweep_is_404(client):
    assert client.get("/api/sweeps/does-not-exist").status_code == 404


def test_sweep_detail_carries_skipped_metric_reason(client):
    body = client.get(f"/api/sweeps/{DEMO}").json()
    skipped = body["manifest"].get("skipped_metrics") or []
    for item in skipped:
        assert item["reason"], "跳过指标必须带原因，否则界面无法解释为什么是 n=0"


def test_aggregate_matches_analysis_module(client):
    """同一份数据在 CLI 与 HTTP 上的口径必须逐字段一致。"""
    body = client.get(
        f"/api/sweeps/{DEMO}/aggregate",
        params={"group_by": "beta_gov", "metrics": ["ord_mean_opinion_final"]},
    ).json()

    expected = aggregate(
        load_results(RESULTS / DEMO),
        group_by=["beta_gov"],
        metrics=["ord_mean_opinion_final"],
        confidence=0.95,
    )
    expected.sort(key=lambda r: float(r["beta_gov"]))
    got = sorted(body["rows"], key=lambda r: float(r["beta_gov"]))

    assert len(got) == len(expected) == 11
    for a, b in zip(got, expected):
        assert a["n"] == b["n"] >= 2, "每个条件必须有重复样本，否则置信区间无从谈起"
        assert a["ord_mean_opinion_final.mean"] == pytest.approx(b["ord_mean_opinion_final.mean"])
        assert a["ord_mean_opinion_final.ci_low"] == pytest.approx(b["ord_mean_opinion_final.ci_low"])
        assert (b["ord_mean_opinion_final.ci_low"]
                <= b["ord_mean_opinion_final.mean"]
                <= b["ord_mean_opinion_final.ci_high"])


def test_aggregate_rejects_unknown_group_key(client):
    resp = client.get(
        f"/api/sweeps/{DEMO}/aggregate", params={"group_by": "not_a_column"}
    )
    assert resp.status_code == 422


def test_curves_carry_per_point_confidence_band(client):
    body = client.get(
        f"/api/sweeps/{DEMO}/curves", params={"group_by": "beta_gov"}
    ).json()
    assert body["available"] is True
    assert body["n_runs"] == 55

    series = body["series"][0]
    column = next(k[: -len(".mean")] for k in series if k.endswith(".mean"))
    steps = series["step"]
    assert len(steps) == len(series[f"{column}.mean"])
    assert len(steps) == len(series[f"{column}.ci_high"])
    assert series[f"{column}.n"] >= 2
    lo, hi, mid = (series[f"{column}.{k}"] for k in ("ci_low", "ci_high", "mean"))
    assert all(l <= m <= h for l, m, h in zip(lo, mid, hi))


def test_curves_report_reason_when_no_timeseries(client, tmp_path):
    """没有时间序列时必须给出可解释的原因，而不是返回一条空曲线。"""
    (tmp_path / "results.csv").write_text("run_id,model,status\nr1,m,ok\n", encoding="utf-8")
    app = odin_api.create_app(tmp_path, mount_web=False)
    with TestClient(app) as c:
        body = c.get("/api/sweeps/../results/aggregate").json()
    assert body["detail"]


def test_responses_are_json_serializable(client):
    """NaN / Inf 不是合法 JSON——宁可缺失，不可失真。"""
    for path in (f"/api/sweeps/{DEMO}/aggregate", f"/api/sweeps/{DEMO}/curves"):
        raw = client.get(path).text
        assert "NaN" not in raw and "Infinity" not in raw
        json.loads(raw)


def _resolve(sweep_id: str):
    from odin_sim.api.deps import resolve_sweep

    return resolve_sweep(RESULTS, sweep_id)


@pytest.mark.parametrize(
    "sweep_id",
    ["../..", "../../..", "..%2F..", "/etc", "ref_demo_dense/../../src"],
)
def test_path_traversal_is_rejected(sweep_id):
    assert _resolve(sweep_id) is None


def test_schema_sweep_reflects_registries(client):
    body = client.get("/api/schema/sweep").json()
    model_names = {m["name"] for m in body["models"]}
    assert model_names >= {"reference-v1"}  # 注册表中的模型必须出现在表单
    assert "reference-v1" in model_names
    assert "beta_gov" in body["run_fields"]
    assert body["topologies"]  # 拓扑 kind 列表非空
    # 默认模板必须是一份合法 SweepSpec
    import odin_sim.experiments.spec as spec_mod
    spec_mod.SweepSpec.from_dict(body["default_template"])


@pytest.mark.parametrize("kind", ["ba", "er", "ring", "ws", "sbm"])
def test_network_generates_per_kind(client, kind):
    body = client.get(
        "/api/network", params={"kind": kind, "n": 80, "seed": 2, "m": 3}
    ).json()
    assert body["kind"] == kind
    assert body["n"] == 80
    assert len(body["nodes"]) == 80
    assert body["hash"]
    # 每个节点都有坐标
    assert all("x" in nd and "y" in nd for nd in body["nodes"])


def test_network_rejects_unknown_kind(client):
    assert client.get("/api/network", params={"kind": "foo"}).status_code == 422


def test_network_rejects_bad_n(client):
    assert client.get("/api/network", params={"kind": "ba", "n": 1}).status_code == 422


def test_submit_invalid_config_is_422(client):
    resp = client.post("/api/runs", json={"config": {"foo": 1}})
    assert resp.status_code == 422


@pytest.mark.parametrize("name", ["../../evil", "..%2F..", "/abs"])
def test_submit_rejects_traversal_name(client, name):
    cfg = {
        "name": name, "model": "reference-v1",
        "base": {"mode": "decisive", "scenario": "polarized", "placement": "ba"},
        "axes": {"beta_gov": [0.0, 1.0]}, "seeds": [1], "store_trajectory": True,
    }
    resp = client.post("/api/runs", json={"config": cfg, "metrics": ["all"]})
    assert resp.status_code == 422


def test_submit_launches_real_run_and_appears_in_sweeps(tmp_path):
    """提交后后台进程真正跑完，且产物被 /api/sweeps 自动发现。"""
    import time

    app = odin_api.create_app(tmp_path, mount_web=False)
    with TestClient(app) as c:
        cfg = {
            "name": "api-test-sweep", "model": "reference-v1",
            "base": {"mode": "decisive", "scenario": "polarized", "placement": "ba"},
            "axes": {"beta_gov": [0.0, 1.0]}, "seeds": [1, 2], "store_trajectory": True,
        }
        started = c.post("/api/runs", json={"config": cfg, "metrics": ["all"]}).json()
        assert started["total"] == 4

        # 轮询直到完成（最多 ~30s）
        task = started["task_id"]
        deadline = time.time() + 30
        final = None
        while time.time() < deadline:
            final = c.get(f"/api/runs/{task}").json()
            if final["state"] != "running":
                break
            time.sleep(0.4)
        assert final["state"] == "done", final
        assert final["done"] == 4

        ids = {s["id"] for s in c.get("/api/sweeps").json()["sweeps"]}
        assert "api-test-sweep" in ids


def test_unknown_task_is_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404

