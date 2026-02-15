from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any

from claude_teams.common._filelock import file_lock
from claude_teams.common._paths import model_to_json, tasks_dir
from claude_teams.common.models import TaskFile
from claude_teams.common.teams import team_exists

TASKS_DIR = Path.home() / ".claude" / "tasks"


_STATUS_ORDER = {"pending": 0, "in_progress": 1, "completed": 2}


def _flush_pending_writes(pending_writes: dict[Path, TaskFile]) -> None:
    for path, task_obj in pending_writes.items():
        path.write_text(model_to_json(task_obj))


def _would_create_cycle(team_dir: Path, from_id: str, to_id: str, pending_edges: dict[str, set[str]]) -> bool:
    """True if making from_id blocked_by to_id creates a cycle.

    BFS from to_id through blocked_by chains (on-disk + pending);
    cycle if it reaches from_id.
    """
    visited: set[str] = set()
    queue = deque([to_id])
    while queue:
        current = queue.popleft()
        if current == from_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        fpath = team_dir / f"{current}.json"
        if fpath.exists():
            task = TaskFile(**json.loads(fpath.read_text()))
            queue.extend(d for d in task.blocked_by if d not in visited)
        queue.extend(d for d in pending_edges.get(current, set()) if d not in visited)
    return False


def next_task_id(team_name: str, base_dir: Path | None = None) -> str:
    team_dir = tasks_dir(base_dir) / team_name
    ids: list[int] = []
    for f in _iter_valid_task_files(team_dir):
        ids.append(int(f.stem))
    return str(max(ids) + 1) if ids else "1"


def create_task(
    team_name: str,
    subject: str,
    description: str,
    active_form: str = "",
    metadata: dict | None = None,
    base_dir: Path | None = None,
) -> TaskFile:
    if not subject or not subject.strip():
        raise ValueError("Task subject must not be empty")
    if not team_exists(team_name, base_dir):
        raise ValueError(f"Team {team_name!r} does not exist")
    team_dir = tasks_dir(base_dir) / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    lock_path = team_dir / ".lock"

    with file_lock(lock_path):
        task_id = next_task_id(team_name, base_dir)
        task = TaskFile(
            id=task_id,
            subject=subject,
            description=description,
            active_form=active_form,
            status="pending",
            metadata=metadata,
        )
        fpath = team_dir / f"{task_id}.json"
        fpath.write_text(model_to_json(task))

    return task


def get_task(team_name: str, task_id: str, base_dir: Path | None = None) -> TaskFile:
    team_dir = tasks_dir(base_dir) / team_name
    fpath = team_dir / f"{task_id}.json"
    raw = json.loads(fpath.read_text())
    return TaskFile(**raw)


def _read_or_pending(path: Path, pending_writes: dict[Path, TaskFile]) -> TaskFile:
    """Read a task from pending_writes cache or disk."""
    if path in pending_writes:
        return pending_writes[path]
    return TaskFile(**json.loads(path.read_text()))


def _iter_valid_task_files(team_dir: Path, exclude_id: str | None = None) -> list[Path]:
    """Iterate through valid task JSON files in team_dir.
    
    Args:
        team_dir: Directory containing task files
        exclude_id: Optional task ID to exclude from results
        
    Returns:
        List of Path objects for valid task files
    """
    result = []
    for f in team_dir.glob("*.json"):
        try:
            int(f.stem)
        except ValueError:
            continue
        if exclude_id is None or f.stem != exclude_id:
            result.append(f)
    return result


def _iter_task_files(team_dir: Path, exclude_id: str) -> list[Path]:
    """List task JSON files in team_dir, excluding the given task ID.
    
    Deprecated: Use _iter_valid_task_files instead.
    """
    return _iter_valid_task_files(team_dir, exclude_id)


def _validate_edge_refs(team_dir: Path, task_id: str, ids: list[str], self_error_msg: str) -> None:
    """Validate that edge target IDs are not self-referencing and exist on disk."""
    for b in ids:
        if b == task_id:
            raise ValueError(self_error_msg)
        if not (team_dir / f"{b}.json").exists():
            raise ValueError(f"Referenced task {b!r} does not exist")


def _build_pending_edges(
    team_dir: Path,
    task_id: str,
    add_blocks: list[str] | None,
    add_blocked_by: list[str] | None,
) -> dict[str, set[str]]:
    """Validate refs exist and build pending edge map for cycle detection."""
    pending_edges: dict[str, set[str]] = {}
    if add_blocks:
        _validate_edge_refs(team_dir, task_id, add_blocks, f"Task {task_id} cannot block itself")
        for b in add_blocks:
            pending_edges.setdefault(b, set()).add(task_id)
    if add_blocked_by:
        _validate_edge_refs(team_dir, task_id, add_blocked_by, f"Task {task_id} cannot be blocked by itself")
        for b in add_blocked_by:
            pending_edges.setdefault(task_id, set()).add(b)
    return pending_edges


def _check_no_cycles(
    team_dir: Path,
    task_id: str,
    add_blocks: list[str] | None,
    add_blocked_by: list[str] | None,
    pending_edges: dict[str, set[str]],
) -> None:
    """Check that proposed edges would not create cycles."""
    if add_blocks:
        for b in add_blocks:
            if _would_create_cycle(team_dir, b, task_id, pending_edges):
                raise ValueError(f"Adding block {task_id} -> {b} would create a circular dependency")
    if add_blocked_by:
        for b in add_blocked_by:
            if _would_create_cycle(team_dir, task_id, b, pending_edges):
                raise ValueError(f"Adding dependency {task_id} blocked_by {b} would create a circular dependency")


def _validate_status_transition(
    team_dir: Path,
    task: TaskFile,
    status: str,
    add_blocked_by: list[str] | None,
) -> None:
    """Validate that a status transition is allowed."""
    cur_order = _STATUS_ORDER[task.status]
    new_order = _STATUS_ORDER.get(status)
    if new_order is None:
        raise ValueError(f"Invalid status: {status!r}")
    if new_order < cur_order:
        raise ValueError(f"Cannot transition from {task.status!r} to {status!r}")
    effective_blocked_by = set(task.blocked_by)
    if add_blocked_by:
        effective_blocked_by.update(add_blocked_by)
    if status in ("in_progress", "completed") and effective_blocked_by:
        for blocker_id in effective_blocked_by:
            blocker_path = team_dir / f"{blocker_id}.json"
            if blocker_path.exists():
                blocker = TaskFile(**json.loads(blocker_path.read_text()))
                if blocker.status != "completed":
                    raise ValueError(
                        f"Cannot set status to {status!r}: blocked by task {blocker_id} (status: {blocker.status!r})"
                    )


def _apply_edges(
    team_dir: Path,
    task: TaskFile,
    task_id: str,
    add_blocks: list[str] | None,
    add_blocked_by: list[str] | None,
    pending_writes: dict[Path, TaskFile],
) -> None:
    """Apply add_blocks/add_blocked_by to task and related tasks (in-memory)."""
    if add_blocks:
        existing = set(task.blocks)
        for b in add_blocks:
            if b not in existing:
                task.blocks.append(b)
                existing.add(b)
            b_path = team_dir / f"{b}.json"
            other = _read_or_pending(b_path, pending_writes)
            if task_id not in other.blocked_by:
                other.blocked_by.append(task_id)
            pending_writes[b_path] = other
    if add_blocked_by:
        existing = set(task.blocked_by)
        for b in add_blocked_by:
            if b not in existing:
                task.blocked_by.append(b)
                existing.add(b)
            b_path = team_dir / f"{b}.json"
            other = _read_or_pending(b_path, pending_writes)
            if task_id not in other.blocks:
                other.blocks.append(task_id)
            pending_writes[b_path] = other


def _apply_metadata(task: TaskFile, metadata: dict[str, Any]) -> None:
    """Merge metadata into task, removing keys set to None."""
    current = task.metadata or {}
    for k, v in metadata.items():
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    task.metadata = current if current else None


def _clean_task_references(
    team_dir: Path, task_id: str, pending_writes: dict[Path, TaskFile], remove_blocks: bool = False
) -> None:
    """Remove task_id from reference lists of other tasks.
    
    Args:
        team_dir: Directory containing task files
        task_id: ID of the task being cleaned up
        pending_writes: Dictionary of pending task updates
        remove_blocks: If True, also remove from blocks lists (for deletion).
                      If False, only remove from blocked_by lists (for completion).
    """
    for f in _iter_task_files(team_dir, task_id):
        other = _read_or_pending(f, pending_writes)
        changed = False
        
        # Always clean blocked_by references
        if task_id in other.blocked_by:
            other.blocked_by.remove(task_id)
            changed = True
        
        # Optionally clean blocks references (on delete)
        if remove_blocks and task_id in other.blocks:
            other.blocks.remove(task_id)
            changed = True
        
        if changed:
            pending_writes[f] = other


def _clean_references_on_complete(team_dir: Path, task_id: str, pending_writes: dict[Path, TaskFile]) -> None:
    """Remove task_id from blocked_by lists of other tasks when completed."""
    _clean_task_references(team_dir, task_id, pending_writes, remove_blocks=False)


def _clean_references_on_delete(team_dir: Path, task_id: str, pending_writes: dict[Path, TaskFile]) -> None:
    """Remove task_id from both blocked_by and blocks lists of other tasks."""
    _clean_task_references(team_dir, task_id, pending_writes, remove_blocks=True)


def _apply_scalar_fields(
    task: TaskFile,
    subject: str | None,
    description: str | None,
    active_form: str | None,
    owner: str | None,
) -> None:
    """Apply simple scalar field updates to task."""
    if subject is not None:
        task.subject = subject
    if description is not None:
        task.description = description
    if active_form is not None:
        task.active_form = active_form
    if owner is not None:
        task.owner = owner


def _apply_status_and_cleanup(
    team_dir: Path,
    task: TaskFile,
    task_id: str,
    status: str | None,
    pending_writes: dict[Path, TaskFile],
) -> None:
    """Apply status change and clean up references in other tasks."""
    if status is not None and status != "deleted":
        task.status = status
        if status == "completed":
            _clean_references_on_complete(team_dir, task_id, pending_writes)
    elif status == "deleted":
        task.status = "deleted"
        _clean_references_on_delete(team_dir, task_id, pending_writes)


def _write_task_updates(
    fpath: Path,
    task: TaskFile,
    status: str | None,
    pending_writes: dict[Path, TaskFile],
) -> None:
    """Flush all pending writes and write/delete the main task file."""
    if status == "deleted":
        _flush_pending_writes(pending_writes)
        fpath.unlink()
    else:
        fpath.write_text(model_to_json(task))
        _flush_pending_writes(pending_writes)


def update_task(
    team_name: str,
    task_id: str,
    *,
    status: str | None = None,
    owner: str | None = None,
    subject: str | None = None,
    description: str | None = None,
    active_form: str | None = None,
    add_blocks: list[str] | None = None,
    add_blocked_by: list[str] | None = None,
    metadata: dict | None = None,
    base_dir: Path | None = None,
) -> TaskFile:
    team_dir = tasks_dir(base_dir) / team_name
    lock_path = team_dir / ".lock"
    fpath = team_dir / f"{task_id}.json"

    with file_lock(lock_path):
        task = TaskFile(**json.loads(fpath.read_text()))

        pending_edges = _build_pending_edges(team_dir, task_id, add_blocks, add_blocked_by)
        _check_no_cycles(team_dir, task_id, add_blocks, add_blocked_by, pending_edges)
        if status is not None and status != "deleted":
            _validate_status_transition(team_dir, task, status, add_blocked_by)

        pending_writes: dict[Path, TaskFile] = {}
        _apply_scalar_fields(task, subject, description, active_form, owner)
        _apply_edges(team_dir, task, task_id, add_blocks, add_blocked_by, pending_writes)
        if metadata is not None:
            _apply_metadata(task, metadata)
        _apply_status_and_cleanup(team_dir, task, task_id, status, pending_writes)
        _write_task_updates(fpath, task, status, pending_writes)

    return task


def list_tasks(team_name: str, base_dir: Path | None = None) -> list[TaskFile]:
    if not team_exists(team_name, base_dir):
        raise ValueError(f"Team {team_name!r} does not exist")
    team_dir = tasks_dir(base_dir) / team_name
    tasks: list[TaskFile] = []
    for f in _iter_valid_task_files(team_dir):
        tasks.append(TaskFile(**json.loads(f.read_text())))
    tasks.sort(key=lambda t: int(t.id))
    return tasks


def reset_owner_tasks(team_name: str, agent_name: str, base_dir: Path | None = None) -> None:
    team_dir = tasks_dir(base_dir) / team_name
    lock_path = team_dir / ".lock"

    with file_lock(lock_path):
        for f in _iter_valid_task_files(team_dir):
            task = TaskFile(**json.loads(f.read_text()))
            if task.owner == agent_name:
                if task.status != "completed":
                    task.status = "pending"
                task.owner = None
                f.write_text(model_to_json(task))
