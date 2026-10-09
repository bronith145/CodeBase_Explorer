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

        repo_name = slugify(source.rstrip("/").removesuffix(".git").split("/")[-1])
        clone_target = workspace / repo_name
        if (clone_target / ".git").exists():
            Repo(clone_target).remotes.origin.pull()
        else:
            workspace.mkdir(parents=True, exist_ok=True)
            Repo.clone_from(source, clone_target, depth=1)
        return clone_target, repo_name
    root_path = Path(source).resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"Not a directory: {root_path}")
    return root_path, slugify(root_path.name)


def scan_python_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of .py files, respecting the root .gitignore."""
    gitignore_lines: list[str] = []
    gitignore_path = root / ".gitignore"
    if gitignore_path.is_file():
        gitignore_lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    ignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", gitignore_lines)

    python_files: list[str] = []
    for dir_path, subdirectories, filenames in os.walk(root):
        relative_dir = Path(dir_path).relative_to(root).as_posix()
        relative_dir = "" if relative_dir == "." else relative_dir
        subdirectories[:] = [
            subdir for subdir in subdirectories
            if subdir not in ALWAYS_IGNORE_DIRS
            and not ignore_spec.match_file(f"{relative_dir}/{subdir}/".lstrip("/"))
        ]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            relative_path = f"{relative_dir}/{filename}".lstrip("/")
            full_path = Path(dir_path) / filename
            if ignore_spec.match_file(relative_path) or full_path.stat().st_size > MAX_FILE_BYTES:
                continue
            python_files.append(relative_path)
    return sorted(python_files)
