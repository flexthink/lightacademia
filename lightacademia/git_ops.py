from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run_git(project_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=project_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise GitError(detail)
    return result


def git_init(project_dir: Path) -> None:
    run_git(project_dir, "init")
    run_git(project_dir, "config", "user.name", "Light Academia")
    run_git(project_dir, "config", "user.email", "lightacademia@local")


def git_has_changes(project_dir: Path) -> bool:
    result = run_git(project_dir, "status", "--porcelain", check=False)
    return bool(result.stdout.strip())


def git_status_lines(project_dir: Path) -> list[str]:
    if not (project_dir / ".git").exists():
        return []
    result = run_git(project_dir, "status", "--porcelain", check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def git_commit_all(project_dir: Path, message: str) -> bool:
    if not (project_dir / ".git").exists():
        return False
    run_git(project_dir, "add", "-A")
    if not git_has_changes(project_dir):
        return False
    run_git(project_dir, "commit", "-m", message)
    return True


def git_tracks(project_dir: Path, path: Path) -> bool:
    if not (project_dir / ".git").exists():
        return False
    result = run_git(
        project_dir,
        "ls-files",
        "--error-unmatch",
        str(path.relative_to(project_dir)),
        check=False,
    )
    return result.returncode == 0


def git_mv(project_dir: Path, source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if git_tracks(project_dir, source):
        run_git(project_dir, "mv", str(source.relative_to(project_dir)), str(target.relative_to(project_dir)))
    else:
        source.rename(target)
