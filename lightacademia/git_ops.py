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
    run_git(project_dir, "branch", "-M", "main")
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


def git_stage_all(project_dir: Path) -> bool:
    if not (project_dir / ".git").exists():
        return False
    run_git(project_dir, "add", "-A")
    return True


def git_remote_url(project_dir: Path, name: str = "origin") -> str | None:
    if not (project_dir / ".git").exists():
        return None
    result = run_git(project_dir, "remote", "get-url", name, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_set_remote_url(project_dir: Path, url: str, name: str = "origin") -> None:
    if not (project_dir / ".git").exists():
        raise GitError("Project is not a git repository.")
    cleaned = url.strip()
    if not cleaned:
        git_remove_remote(project_dir, name)
        return
    if git_remote_url(project_dir, name) is None:
        run_git(project_dir, "remote", "add", name, cleaned)
    else:
        run_git(project_dir, "remote", "set-url", name, cleaned)


def git_remove_remote(project_dir: Path, name: str = "origin") -> None:
    if not (project_dir / ".git").exists():
        return
    if git_remote_url(project_dir, name) is not None:
        run_git(project_dir, "remote", "remove", name)


def git_has_unmerged_paths(project_dir: Path) -> bool:
    for line in git_status_lines(project_dir):
        if len(line) >= 2 and (line[0] == "U" or line[1] == "U" or line[:2] in {"AA", "DD"}):
            return True
    return False


def git_current_branch(project_dir: Path) -> str:
    result = run_git(project_dir, "branch", "--show-current", check=False)
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch:
        raise GitError("Could not determine current git branch.")
    return branch


def git_remote_branch_exists(project_dir: Path, remote: str, branch: str) -> bool:
    result = run_git(project_dir, "ls-remote", "--heads", remote, branch, check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def git_remote_default_branch(project_dir: Path, remote: str) -> str | None:
    result = run_git(project_dir, "ls-remote", "--symref", remote, "HEAD", check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    return None


def git_sync_branch(project_dir: Path, remote: str) -> str:
    branch = git_current_branch(project_dir)
    if branch == "master" and not git_remote_branch_exists(project_dir, remote, "master"):
        default_branch = git_remote_default_branch(project_dir, remote)
        if default_branch in {None, "main"}:
            run_git(project_dir, "branch", "-M", "main")
            return "main"
    return branch


@dataclass(frozen=True)
class GitSyncResult:
    pulled: bool
    pushed: bool
    committed: bool
    conflicts: bool
    message: str = ""


def git_sync(project_dir: Path, remote: str = "origin") -> GitSyncResult:
    if git_remote_url(project_dir, remote) is None:
        raise GitError("No remote URL configured.")

    git_stage_all(project_dir)
    committed = git_commit_all(project_dir, "Sync checkpoint")
    branch = git_sync_branch(project_dir, remote)

    pulled = False
    if git_remote_branch_exists(project_dir, remote, branch):
        pull = run_git(project_dir, "pull", "--no-rebase", remote, branch, check=False)
        pulled = pull.returncode == 0
        conflicts = git_has_unmerged_paths(project_dir)
        if pull.returncode != 0 and not conflicts:
            detail = pull.stderr.strip() or pull.stdout.strip() or "git pull failed"
            raise GitError(detail)
    else:
        conflicts = False

    if conflicts:
        return GitSyncResult(
            pulled=pulled,
            pushed=False,
            committed=committed,
            conflicts=True,
            message="Sync paused with merge conflicts. Resolve them in the project notes, then sync again.",
        )

    push = run_git(project_dir, "push", "-u", remote, branch, check=False)
    if push.returncode != 0:
        detail = push.stderr.strip() or push.stdout.strip() or "git push failed"
        raise GitError(detail)

    return GitSyncResult(
        pulled=pulled,
        pushed=True,
        committed=committed,
        conflicts=False,
        message="Sync complete.",
    )


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
