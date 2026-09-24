"""当前工作区本地修改范围读取器；只读，不修改 Git 状态。"""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _git_names(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def current_worktree_scope() -> set[str]:
    """返回当前工作区已修改或新增的相对路径。"""
    return _git_names("diff", "--name-only") | _git_names(
        "ls-files", "--others", "--exclude-standard"
    )


def is_in_current_worktree_scope(path: str) -> bool:
    return path.replace("\\", "/") in current_worktree_scope()
