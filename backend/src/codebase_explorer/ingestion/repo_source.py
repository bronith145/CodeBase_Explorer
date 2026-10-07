"""Repository source handling: local path / git clone and .gitignore-aware scanning (ING-1)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pathspec

ALWAYS_IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
    "site-packages", ".mypy_cache", ".ruff_cache", ".pytest_cache",
}
MAX_FILE_BYTES = 1_000_000


def is_git_url(source: str) -> bool:
    return bool(re.match(r"^(https?://|git@|ssh://)", source))


def slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", text).strip("-").lower()


def resolve_source(source: str, workspace: Path) -> tuple[Path, str]:
    """Return (local root path, default repo_id). Clones git URLs (shallow) into workspace."""
    if is_git_url(source):
        from git import Repo  # lazy: only needed for URLs

        name = slugify(source.rstrip("/").removesuffix(".git").split("/")[-1])
        target = workspace / name
        if (target / ".git").exists():
            Repo(target).remotes.origin.pull()
        else:
            workspace.mkdir(parents=True, exist_ok=True)
            Repo.clone_from(source, target, depth=1)
        return target, name
    root = Path(source).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")
    return root, slugify(root.name)


def scan_python_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of .py files, respecting the root .gitignore."""
    lines: list[str] = []
    gi = root / ".gitignore"
    if gi.is_file():
        lines = gi.read_text(encoding="utf-8", errors="ignore").splitlines()
    spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)

    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [
            d for d in dirnames
            if d not in ALWAYS_IGNORE_DIRS
            and not spec.match_file(f"{rel_dir}/{d}/".lstrip("/"))
        ]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = f"{rel_dir}/{fn}".lstrip("/")
            full = Path(dirpath) / fn
            if spec.match_file(rel) or full.stat().st_size > MAX_FILE_BYTES:
                continue
            found.append(rel)
    return sorted(found)

