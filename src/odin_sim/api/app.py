"""FastAPI 应用工厂。

产物根目录挂在 ``app.state.results_root`` 上，路由从这里取——服务本身不持有
全局状态，便于测试时为每个用例构造独立根目录。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .. import __version__
from .deps import resolve_results_root
from .routes_meta import router as meta_router
from .routes_runs import router as runs_router
from .routes_sweeps import router as sweeps_router

WEB_DIRNAME = "web"


def create_app(results_root: str | Path | None = None, *, mount_web: Path | str | None | bool = True) -> FastAPI:
    """构造服务实例。

    参数
    ----
    results_root:
        产物根目录；缺省取 ``ODIN_RESULTS_ROOT`` 或当前目录下的 ``results/``。
    mount_web:
        前端静态目录。``True`` 表示"存在则挂载"，传路径则挂载该路径，
        ``False`` 表示只提供 JSON API（测试与纯后端场景）。
    """
    app = FastAPI(
        title="ODIN-Sim Research Workbench",
        version=__version__,
        summary="Read-only HTTP interface over stored sweep artefacts",
    )
    app.state.results_root = resolve_results_root(results_root)
    app.state.tasks = {}  # 后台 sweep 任务表（进程内）：task_id -> 记录

    app.include_router(meta_router)
    app.include_router(sweeps_router)
    app.include_router(runs_router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "results_root": str(app.state.results_root)}

    if mount_web is not False:
        web_dir = Path(mount_web) if isinstance(mount_web, (str, Path)) else _infer_web_dir()
        if web_dir is not None and web_dir.is_dir():
            from fastapi.staticfiles import StaticFiles

            app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


def _infer_web_dir() -> Path | None:
    """``src/odin_sim/api/app.py`` → 仓库根 → ``web/``。安装成 wheel 时该目录不存在，静默跳过即可。"""
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / WEB_DIRNAME
    return candidate if candidate.is_dir() else None
