"""实验元信息采集。

规则：只记录"能被采集到的事实"；采集不到的一律写为 ``UNKNOWN``，
不得静默省略字段，也不得用占位值冒充真实信息。
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence

UNKNOWN = "unknown"

__all__ = [
    "UNKNOWN",
    "git_commit",
    "git_dirty",
    "python_info",
    "dependency_versions",
    "config_fingerprint",
    "snapshot",
]


def _run_git(args: Sequence[str], cwd: str | Path | None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode, proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def git_commit(cwd: str | Path | None = None) -> str:
    """返回 HEAD 的 commit hash；非 git 仓库或失败时返回 UNKNOWN。"""
    code, out = _run_git(["rev-parse", "HEAD"], cwd)
    return out if code == 0 and out else UNKNOWN


def git_dirty(cwd: str | Path | None = None) -> bool | None:
    """工作区是否有未提交改动；无法判定时返回 None（不要猜 False）。"""
    code, out = _run_git(["status", "--porcelain"], cwd)
    if code != 0:
        return None
    return bool(out)


def python_info() -> dict:
    return {
        "python_version": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def dependency_versions(packages: Sequence[str]) -> dict:
    """返回 {包名: 版本}；未安装的包记为 UNKNOWN。"""
    out: dict[str, str] = {}
    for name in packages:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = UNKNOWN
    return out


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """配置内容指纹：与键顺序无关的 sha1 前 16 位。

    用于 run_id 与"配置是否被改动"的判定。非 JSON 原生类型经 str() 兜底，
    因此该指纹只对可 JSON 化的配置稳定。
    """
    payload = json.dumps(config, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def snapshot(
    packages: Sequence[str] = ("numpy",),
    extra: Mapping[str, Any] | None = None,
    cwd: str | Path | None = None,
) -> dict:
    """采集一次运行的完整环境快照。"""
    info: dict[str, Any] = {
        "git_commit": git_commit(cwd),
        "git_dirty": git_dirty(cwd),
        **python_info(),
        "dependencies": dependency_versions(packages),
    }
    if extra:
        info["extra"] = dict(extra)
    return info
