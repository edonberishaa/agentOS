"""
services/workspace_manager.py — WorkspaceManager: Git worktree lifecycle for tasks.

Each task that actually runs executes inside its own Git worktree on its own
branch (`ag/{task_id}/{agent_id}`), so agents working in parallel never
collide on the same working directory. This service is the only place that
shells out to Git (via GitPython) — routers and other services go through it
rather than running `git` themselves.

The base branch isn't persisted anywhere (the `tasks` table has no column for
it), so `get_diff`/`merge`/`rollback` re-resolve it at call time as the main
repo's current branch (falling back to `main`/`master` on detached HEAD).
This assumes a single, stable base branch per repo for this phase.

`merge` requires the main repo to currently be checked out on the base
branch — it will not switch the user's own working tree branch as a side
effect of a REST call. Spawning the actual agent process, credential
monitoring, the approval engine, context pack generation, and conflict
detection are all out of scope here (Phase 3 Day 4+).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from git import GitCommandError, InvalidGitRepositoryError, Repo

from ..database import execute, fetch_one, get_db
from ..events import event_ledger
from ..models import DiffResponse, MergeResponse, RollbackResponse, TaskResponse, WorkspaceState
from .task_service import task_service

logger = logging.getLogger(__name__)

WORKSPACES_DIR = Path(".agentos") / "workspaces"


class WorkspaceError(Exception):
    """Raised when a Git worktree operation can't be completed."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when the task (or its workspace) doesn't exist — maps to HTTP 404."""


def find_repo() -> Repo:
    """Locate the enclosing Git repo by walking up from CWD — never assume CWD is the root."""
    try:
        return Repo(Path.cwd(), search_parent_directories=True)
    except InvalidGitRepositoryError as exc:
        raise WorkspaceError(
            "Not inside a Git repository — Agent OS requires the project to be a Git "
            "repo for workspace isolation."
        ) from exc


def resolve_base_branch(repo: Repo) -> str:
    """The branch new task worktrees fork from — the repo's current branch.

    Falls back to 'main'/'master' if HEAD is detached, since there's no
    "current branch" to read in that case.
    """
    try:
        return str(repo.active_branch.name)
    except TypeError as exc:
        for candidate in ("main", "master"):
            if candidate in repo.heads:
                return candidate
        raise WorkspaceError(
            "Repo HEAD is detached and no main/master branch exists — cannot "
            "determine a base branch"
        ) from exc


def _branch_name(task_id: str, agent_id: str) -> str:
    return f"ag/{task_id}/{agent_id}"


async def _get_task_or_raise(task_id: str) -> TaskResponse:
    task = await task_service.get(task_id)
    if task is None:
        raise WorkspaceNotFoundError(f"Task {task_id} not found")
    return task


async def get_latest_run_id(task_id: str) -> str | None:
    """Most recent AgentRun for this task, or None if it has never run."""
    async with get_db() as db:
        row = await fetch_one(
            db,
            "SELECT id FROM agent_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT 1",
            (task_id,),
        )
    return str(row["id"]) if row else None


def _diff_files(repo: Repo, base_branch: str, branch: str) -> list[str]:
    names = repo.git.diff("--name-only", f"{base_branch}...{branch}")
    return [line for line in names.splitlines() if line]


class WorkspaceManager:
    """Git worktree operations backing task workspace isolation."""

    async def create_worktree(self, task_id: str, agent_id: str, base_branch: str) -> str:
        """Create (or reuse) the Git worktree + branch for a task. Returns the worktree path."""
        task = await _get_task_or_raise(task_id)

        if task.worktree_path:
            logger.info(
                "Worktree for task %s already exists at %s — skipping creation",
                task_id,
                task.worktree_path,
            )
            return task.worktree_path

        repo = find_repo()
        if repo.working_tree_dir is None:
            raise WorkspaceError("Repo has no working tree (bare repo) — cannot create a worktree")

        repo_root = Path(repo.working_tree_dir)
        worktree_path = repo_root / WORKSPACES_DIR / task_id
        branch = _branch_name(task_id, agent_id)

        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            repo.git.worktree("add", "-b", branch, str(worktree_path), base_branch)
        except GitCommandError as exc:
            raise WorkspaceError(f"git worktree add failed: {exc}") from exc

        async with get_db() as db:
            await execute(
                db,
                "UPDATE tasks SET branch = ?, worktree_path = ? WHERE id = ?",
                (branch, str(worktree_path), task_id),
            )

        await event_ledger.emit(
            source="gateway",
            type="workspace.created",
            payload={"task_id": task_id, "branch": branch, "worktree_path": str(worktree_path)},
            mission_id=task.mission_id,
            task_id=task_id,
        )

        return str(worktree_path)

    async def freeze(self, task_id: str, reason: str) -> dict[str, Any]:
        """Commit any dirty state in the task's worktree and mark the task paused."""
        task = await _get_task_or_raise(task_id)
        if not task.worktree_path:
            raise WorkspaceNotFoundError(f"Task {task_id} has no workspace to freeze")

        run_id = await get_latest_run_id(task_id)
        worktree_path = Path(task.worktree_path)

        workspace_state: WorkspaceState
        wip_commit_sha: str | None = None

        if not worktree_path.exists():
            logger.warning(
                "Worktree for task %s no longer exists on disk (%s) — skipping WIP commit",
                task_id,
                worktree_path,
            )
            workspace_state = "unknown"
        else:
            worktree_repo = Repo(worktree_path)
            try:
                if worktree_repo.is_dirty(untracked_files=True):
                    message = f"WIP: agent paused — {reason} {run_id or 'unknown-run'}"
                    try:
                        worktree_repo.git.add("-A")
                        worktree_repo.git.commit("-m", message)
                        wip_commit_sha = str(worktree_repo.head.commit.hexsha)
                    except GitCommandError as exc:
                        logger.warning("WIP commit failed for task %s: %s", task_id, exc)
                    workspace_state = "partially_modified" if worktree_repo.is_dirty(
                        untracked_files=True
                    ) else "clean"
                else:
                    workspace_state = "clean"
            finally:
                # Windows holds file handles open on a Repo until it's explicitly
                # closed — leaving this open would block a later worktree removal.
                worktree_repo.close()

        async with get_db() as db:
            await execute(db, "UPDATE tasks SET status = 'paused' WHERE id = ?", (task_id,))
            if run_id:
                await execute(
                    db,
                    "UPDATE agent_runs SET workspace_state = ? WHERE id = ?",
                    (workspace_state, run_id),
                )

        await event_ledger.emit(
            source="gateway",
            type="workspace.frozen",
            payload={
                "task_id": task_id,
                "reason": reason,
                "wip_commit_sha": wip_commit_sha,
                "workspace_state": workspace_state,
            },
            mission_id=task.mission_id,
            task_id=task_id,
        )

        return {"workspace_state": workspace_state, "wip_commit_sha": wip_commit_sha}

    async def get_diff(self, task_id: str) -> DiffResponse:
        """Diff of the task's branch against the current base branch."""
        task = await _get_task_or_raise(task_id)
        if not task.branch:
            raise WorkspaceNotFoundError(f"Task {task_id} has no workspace yet")

        repo = find_repo()
        base_branch = resolve_base_branch(repo)

        try:
            diff_text = repo.git.diff(f"{base_branch}...{task.branch}")
            files_changed = _diff_files(repo, base_branch, task.branch)
        except GitCommandError as exc:
            raise WorkspaceError(f"git diff failed: {exc}") from exc

        return DiffResponse(
            task_id=task_id,
            branch=task.branch,
            base_branch=base_branch,
            diff_text=diff_text,
            files_changed=files_changed,
        )

    async def merge(self, task_id: str) -> MergeResponse:
        """Merge the task's branch into the base branch and remove its worktree."""
        task = await _get_task_or_raise(task_id)
        if not task.branch:
            raise WorkspaceNotFoundError(f"Task {task_id} has no workspace yet")

        repo = find_repo()
        base_branch = resolve_base_branch(repo)

        try:
            current_branch = repo.active_branch.name
        except TypeError as exc:
            raise WorkspaceError(
                "Main repo HEAD is detached — checkout the base branch before merging"
            ) from exc
        if current_branch != base_branch:
            raise WorkspaceError(
                f"Main repo is on '{current_branch}', not base branch '{base_branch}' — "
                "checkout the base branch before merging"
            )

        files_changed = _diff_files(repo, base_branch, task.branch)

        try:
            repo.git.merge(
                task.branch, "--no-ff", "-m", f"Merge task {task_id} ({task.branch})"
            )
        except GitCommandError as exc:
            raise WorkspaceError(f"git merge failed (possible conflict): {exc}") from exc

        merged_sha = str(repo.head.commit.hexsha)
        worktree_removed = self._remove_worktree(repo, task_id, task.worktree_path)

        completed_at = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await execute(
                db,
                "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ?",
                (completed_at, task_id),
            )

        await event_ledger.emit(
            source="gateway",
            type="workspace.merged",
            payload={
                "task_id": task_id,
                "merged_sha": merged_sha,
                "files_changed": len(files_changed),
            },
            mission_id=task.mission_id,
            task_id=task_id,
        )

        return MergeResponse(
            task_id=task_id, merged_sha=merged_sha, worktree_removed=worktree_removed
        )

    async def rollback(self, task_id: str, run_id: str) -> RollbackResponse:
        """Discard the task's branch back to its last clean (pre-agent) commit."""
        task = await _get_task_or_raise(task_id)
        if not task.branch:
            raise WorkspaceNotFoundError(f"Task {task_id} has no workspace yet")

        repo = find_repo()
        base_branch = resolve_base_branch(repo)

        try:
            merge_base_sha = repo.git.merge_base(base_branch, task.branch).strip()
        except GitCommandError as exc:
            raise WorkspaceError(f"git merge-base failed: {exc}") from exc

        worktree_path = Path(task.worktree_path) if task.worktree_path else None
        worktree_removed = False

        if worktree_path is None:
            logger.warning("Task %s has no worktree_path recorded — skipping reset", task_id)
        elif not worktree_path.exists():
            logger.warning(
                "Worktree for task %s no longer exists on disk (%s) — skipping reset",
                task_id,
                worktree_path,
            )
        else:
            try:
                worktree_repo = Repo(worktree_path)
            except InvalidGitRepositoryError:
                logger.warning(
                    "Worktree for task %s at %s is no longer a valid Git repo "
                    "(likely left over from a previous failed removal) — skipping reset",
                    task_id,
                    worktree_path,
                )
            else:
                try:
                    worktree_repo.git.reset("--hard", merge_base_sha)
                except GitCommandError as exc:
                    raise WorkspaceError(f"git reset failed: {exc}") from exc
                finally:
                    # Windows holds file handles open on a Repo until it's explicitly
                    # closed — leaving this open would block the worktree removal below.
                    worktree_repo.close()
                worktree_removed = self._remove_worktree(repo, task_id, task.worktree_path)

        completed_at = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await execute(
                db,
                "UPDATE tasks SET status = 'failed', completed_at = ? WHERE id = ?",
                (completed_at, task_id),
            )

        await event_ledger.emit(
            source="gateway",
            type="workspace.rolled_back",
            payload={"task_id": task_id, "run_id": run_id, "rolled_back_to_sha": merge_base_sha},
            mission_id=task.mission_id,
            task_id=task_id,
        )

        return RollbackResponse(
            task_id=task_id, rolled_back_to_sha=merge_base_sha, worktree_removed=worktree_removed
        )

    def _remove_worktree(self, repo: Repo, task_id: str, worktree_path: str | None) -> bool:
        if not worktree_path:
            return False
        path = Path(worktree_path)
        if not path.exists():
            logger.warning(
                "Worktree for task %s no longer exists on disk (%s) — skipping removal",
                task_id,
                path,
            )
            return False
        try:
            repo.git.worktree("remove", str(path), "--force")
            return True
        except GitCommandError as exc:
            logger.warning("Failed to remove worktree for task %s: %s", task_id, exc)
            return False


# Module-level singleton — imported directly by routers, mirroring other services.
workspace_manager = WorkspaceManager()
