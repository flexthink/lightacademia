from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitFileRevision:
    commit: str
    committed_at: datetime
    author: str
    subject: str


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


def git_file_history(project_dir: Path, path: Path, limit: int = 50) -> list[GitFileRevision]:
    if not (project_dir / ".git").exists():
        return []
    relative_path = str(path.relative_to(project_dir))
    result = run_git(
        project_dir,
        "log",
        "--follow",
        f"--max-count={limit}",
        "--format=%H%x09%ct%x09%an%x09%s",
        "--",
        relative_path,
        check=False,
    )
    if result.returncode != 0:
        return []

    revisions = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        commit, timestamp, author, subject = parts
        try:
            committed_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone()
        except ValueError:
            continue
        revisions.append(GitFileRevision(commit, committed_at, author, subject))
    return revisions


def git_file_at_revision(project_dir: Path, path: Path, commit: str) -> str:
    relative_path = str(path.relative_to(project_dir))
    result = run_git(project_dir, "show", f"{commit}:{relative_path}")
    return result.stdout
