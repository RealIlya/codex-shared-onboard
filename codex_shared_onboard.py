#!/usr/bin/env python3
"""Onboard a machine into a shared Codex layer.

Dry-run is the default. Real filesystem changes require --apply.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import ntpath
import os
import platform
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


APP_NAME = "codex-shared-onboard"
APP_VERSION = "0.4.0"
CODEX_ANALYSIS_SEPARATOR = "--- Codex analysis ---"
DEFAULT_SYNCTHING_URL = "http://127.0.0.1:8384"
MEMORIES_PUBLISHED_DIR_NAME = "memories-published"
MEMORIES_CURRENT_DIR_NAME = "current"
MEMORIES_SNAPSHOTS_DIR_NAME = "snapshots"
MEMORIES_MANIFEST_NAME = "manifest.json"
MAIN_HELP_EPILOG = f"Run '{APP_NAME} <command> --help' for detailed command behavior."
DOCTOR_HELP_EPILOG = """Codex analysis behavior:
  --codex
    Captures doctor diagnostics and sends them to 'codex exec' for explanation.

  --codex-only
    Suppresses raw doctor diagnostics and prints only the Codex analysis.

  --codex-read-repo
    Adds '-C <current-working-directory>' so Codex may read this repository in read-only mode.

  --codex-profile PROFILE
    Passes '-p PROFILE' to Codex CLI.

  --codex-model MODEL
    Passes '-m MODEL' to Codex CLI.

  --codex-extra-prompt TEXT
    Appends extra user instructions to the default English analysis prompt.

Runtime guarantees:
  Codex is invoked with '--ask-for-approval never exec --ephemeral --sandbox read-only --color never'.
  On success, only the last Codex message is printed. On failure, transcript output is filtered to error-like lines.
"""
INSTALL_HELP_EPILOG = """Install behavior:
  Creates .codex-shared, .codex-shared/skills-user, and .codex-shared/tools.
  Writes .codex-shared/.stignore and .codex-shared/memory-policy.md if missing.
  Links every shared skill directory from .codex-shared/skills-user into .codex/skills.
  Existing local skills are backed up before replacement when --apply is used.
"""
MEMORIES_HELP_EPILOG = """Memory sharing commands:
  adopt (DEPRECATED)
    Use on the source/writer machine when local .codex/memories is still the source of truth.
    Prefer publish for new copy-based setups.

  link (DEPRECATED)
    Use on additional machines after .codex-shared/memories already exists.
    Prefer consume for new copy-based setups.

  publish
    Use on the writer machine to copy local .codex/memories into
    .codex-shared/memories-published/current and create a snapshot.

  consume
    Use on reader machines to replace local .codex/memories with a validated
    copy of .codex-shared/memories-published/current.
"""
MEMORIES_ADOPT_HELP_EPILOG = """DEPRECATED:
  Prefer 'memories publish' for new copy-based memories sharing.

Adopt behavior:
  Requires a real local .codex/memories directory and no existing .codex-shared/memories.
  Copies local memories to .codex-shared/memories, backs up the original local directory,
  then links .codex/memories to .codex-shared/memories.
"""
MEMORIES_LINK_HELP_EPILOG = """DEPRECATED:
  Prefer 'memories consume' for new copy-based memories sharing.

Link behavior:
  Requires an existing .codex-shared/memories directory.
  Backs up an existing local .codex/memories directory, then links .codex/memories to shared memories.
"""
MEMORIES_PUBLISH_HELP_EPILOG = """Publish behavior:
  Requires a real local .codex/memories directory.
  Refuses conflict-like memory files.
  Copies local memories to .codex-shared/memories-published/current.
  Writes a manifest.json with file hashes and keeps a timestamped snapshot.
"""
MEMORIES_CONSUME_HELP_EPILOG = """Consume behavior:
  Requires .codex-shared/memories-published/current with a valid manifest.json.
  Backs up an existing local .codex/memories path, including legacy links.
  Replaces local memories with a real copied directory, not a shared link.
"""
SNAPSHOT_HELP_EPILOG = """Snapshot behavior:
  Initializes or reuses a Git repository in .codex-shared and commits the current shared state.
  Dry-run is the default; use --apply to write the snapshot.
"""
INSTALL_CLI_HELP_EPILOG = """Launcher behavior:
  Installs a codex-shared-onboard wrapper into --bin-dir.
  On Windows, the launcher is a .cmd file; on Unix-like systems, it is an executable script.
  PATH is updated when supported unless --no-path-update is set.
"""
STIGNORE_TEXT = """(?d)**/__pycache__
(?d)**/*.pyc
(?d)**/.pytest_cache
(?d)**/.mypy_cache
(?d)**/.ruff_cache
(?d)**/node_modules
(?d)**/.DS_Store
(?d)**/Thumbs.db
(?d)**/desktop.ini
(?d)**/*sync-conflict*
(?d)**/*.tmp
(?d)**/*.log
(?d)**/.system
"""

MEMORY_POLICY_TEXT = """# Codex Shared Memory Policy

Official Codex memories are generated state.

Rules:
- Use one writer and many readers.
- Writer: use_memories=true and generate_memories=true.
- Readers: use_memories=true and generate_memories=false.
- Do not silently resolve Syncthing conflict files.
- If memory conflicts exist, mention them before relying on memory-derived assumptions.
- Do not manually edit MEMORY.md, memory_summary.md, or raw_memories.md unless explicitly requested.
- Preferred WSL/Linux model: keep .codex/memories local, publish writer copies to
  .codex-shared/memories-published/current, and consume validated copies on readers.
- Legacy model: .codex/memories may be linked to .codex-shared/memories as a whole
  directory only when the symlink/junction tradeoff is explicitly accepted.
"""

MEMORY_KEY_FILES = ("MEMORY.md", "memory_summary.md", "raw_memories.md")
WINDOWS_PATH_REGISTRY_KEY = "Environment"
WINDOWS_PATH_VALUE_NAME = "Path"


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def is_windows() -> bool:
    return os.name == "nt"


def is_wsl() -> bool:
    if is_windows():
        return False
    try:
        data = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "microsoft" in data or "wsl" in data


def default_codex_dir() -> Path:
    return Path.home() / ".codex"


def detect_wsl_windows_user() -> str | None:
    if not is_wsl():
        return None
    users_dir = Path("/mnt/c/Users")
    if not users_dir.exists():
        return None
    try:
        children = list(users_dir.iterdir())
    except OSError:
        return None

    candidates = []
    for child in children:
        if child.name.lower() in {"public", "default", "default user", "all users"}:
            continue
        if (child / ".codex-shared").exists():
            candidates.append(child.name)
    candidates.sort(key=str.casefold)
    return candidates[0] if len(candidates) == 1 else None


def default_shared_dir() -> Path:
    if is_wsl():
        win_user = detect_wsl_windows_user()
        if win_user:
            return Path("/mnt/c/Users") / win_user / ".codex-shared"
    return Path.home() / ".codex-shared"


def default_bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


@dataclass
class Context:
    codex_dir: Path
    shared_dir: Path
    apply: bool
    verbose: bool = False
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def skills_dir(self) -> Path:
        return self.codex_dir / "skills"

    @property
    def skills_backups_dir(self) -> Path:
        return self.codex_dir / "skills-backups"

    @property
    def shared_skills_dir(self) -> Path:
        return self.shared_dir / "skills-user"

    @property
    def memories_dir(self) -> Path:
        return self.codex_dir / "memories"

    @property
    def shared_memories_dir(self) -> Path:
        return self.shared_dir / "memories"

    @property
    def published_memories_dir(self) -> Path:
        return self.shared_dir / MEMORIES_PUBLISHED_DIR_NAME

    @property
    def published_current_dir(self) -> Path:
        return self.published_memories_dir / MEMORIES_CURRENT_DIR_NAME

    @property
    def published_snapshots_dir(self) -> Path:
        return self.published_memories_dir / MEMORIES_SNAPSHOTS_DIR_NAME

    def info(self, message: str) -> None:
        print(message)

    def plan(self, message: str) -> None:
        prefix = "APPLY" if self.apply else "DRY-RUN"
        line = f"[{prefix}] {message}"
        self.actions.append(line)
        print(line)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")

    def error(self, message: str) -> None:
        self.errors.append(message)
        print(f"[ERROR] {message}")


def ensure_dir(ctx: Context, path: Path) -> bool:
    if ctx.errors:
        return False
    try:
        exists = path.exists()
        is_dir = path.is_dir() if exists else False
    except OSError as exc:
        ctx.error(f"Failed to inspect directory {path}: {exc}")
        return False
    if exists:
        if not is_dir:
            ctx.error(f"Expected directory but found file: {path}")
            return False
        return True
    ctx.plan(f"create directory {path}")
    if ctx.apply:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ctx.error(f"Failed to create directory {path}: {exc}")
            return False
    return True


def write_text_if_missing(ctx: Context, path: Path, text: str) -> None:
    if ctx.errors:
        return
    try:
        parent_exists = path.parent.exists()
        parent_is_dir = path.parent.is_dir() if parent_exists else False
        path_exists = path.exists()
        path_is_dir = path.is_dir() if path_exists else False
    except OSError as exc:
        ctx.error(f"Failed to inspect file path {path}: {exc}")
        return
    if parent_exists and not parent_is_dir:
        ctx.error(f"Expected parent directory but found file: {path.parent}")
        return
    if path_exists:
        if path_is_dir:
            ctx.error(f"Expected file but found directory: {path}")
        return
    ctx.plan(f"write {path}")
    if ctx.apply:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            ctx.error(f"Failed to write {path}: {exc}")


def iter_shared_skills(ctx: Context) -> Iterable[Path]:
    if not ctx.shared_skills_dir.exists():
        return
    if not ctx.shared_skills_dir.is_dir():
        ctx.error(f"Expected shared skills directory but found file: {ctx.shared_skills_dir}")
        return

    for path in sorted(ctx.shared_skills_dir.iterdir(), key=lambda item: item.name.casefold()):
        if path.name == ".system":
            continue
        if not path.is_dir():
            continue
        if not (path / "SKILL.md").is_file():
            ctx.warn(f"Skipping invalid shared skill without SKILL.md: {path}")
            continue
        yield path


def same_resolved_path(left: Path, right: Path) -> bool:
    try:
        left_text = str(left.resolve(strict=False))
        right_text = str(right.resolve(strict=False))
    except (OSError, RuntimeError):
        return False
    if is_windows():
        left_text = os.path.normcase(left_text)
        right_text = os.path.normcase(right_text)
    return left_text == right_text


def next_backup_path(path: Path) -> Path:
    base = path.with_name(f"{path.name}.bak-local-{now_stamp()}")
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = path.with_name(f"{base.name}-{index}")
        if not candidate.exists():
            return candidate
        index += 1


def next_backup_path_in_dir(path: Path, backup_dir: Path) -> Path:
    base = backup_dir / f"{path.name}.bak-local-{now_stamp()}"
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = backup_dir / f"{base.name}-{index}"
        if not candidate.exists():
            return candidate
        index += 1


def next_pending_link_path(path: Path) -> Path:
    base = path.with_name(f"{path.name}.link-pending-{now_stamp()}")
    if not base.exists() and not base.is_symlink():
        return base
    index = 1
    while True:
        candidate = path.with_name(f"{base.name}-{index}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        index += 1


def create_windows_junction(ctx: Context, link: Path, target: Path) -> bool:
    ctx.plan(f"create junction {link} -> {target}")
    if not ctx.apply:
        return True
    link.parent.mkdir(parents=True, exist_ok=True)
    last_detail = ""
    for attempt in range(1, 6):
        move_empty_link_stub(ctx, link)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        last_detail = (result.stderr or result.stdout).strip()
        if "already exists" not in last_detail.casefold():
            break
        if not move_empty_link_stub(ctx, link):
            break
        if attempt < 5:
            time.sleep(0.1)
    ctx.error(f"Failed to create junction {link} -> {target}: {last_detail}")
    return False


def move_empty_link_stub(ctx: Context, link: Path) -> bool:
    if not path_exists_or_link(link) or link.is_symlink():
        return False
    try:
        is_dir = link.is_dir()
        is_empty = is_dir and not any(link.iterdir())
    except OSError:
        return False
    if not is_empty:
        return False
    backup = link.with_name(f"{link.name}.link-stub-bak-{now_stamp()}")
    index = 1
    while backup.exists() or backup.is_symlink():
        backup = link.with_name(f"{link.name}.link-stub-bak-{now_stamp()}-{index}")
        index += 1
    ctx.warn(f"Empty link stub exists at {link}; preserving it as {backup}")
    return rename_path(ctx, link, backup, "empty link stub")


def create_directory_link(ctx: Context, link: Path, target: Path) -> bool:
    ctx.plan(f"create directory link {link} -> {target}")
    if not ctx.apply:
        return True
    link.parent.mkdir(parents=True, exist_ok=True)
    if is_windows():
        return create_windows_junction(ctx, link, target)
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        if move_empty_link_stub(ctx, link):
            try:
                os.symlink(target, link, target_is_directory=True)
                return True
            except OSError as retry_exc:
                exc = retry_exc
        ctx.error(f"Failed to create symlink {link} -> {target}: {exc}")
        return False
    return True


def remove_pending_link(ctx: Context, pending: Path) -> None:
    if not ctx.apply:
        return
    if not pending.exists() and not pending.is_symlink():
        return
    try:
        if pending.is_dir() and not pending.is_symlink():
            pending.rmdir()
        else:
            pending.unlink()
    except OSError as exc:
        ctx.error(f"Failed to clean up staged link {pending}: {exc}")


def restore_symlink(ctx: Context, link: Path, old_target: str) -> None:
    if link.exists() or link.is_symlink():
        return
    try:
        os.symlink(old_target, link, target_is_directory=True)
    except OSError as exc:
        ctx.error(f"Failed to restore original symlink {link} -> {old_target}: {exc}")


def replace_wrong_symlink(ctx: Context, link: Path, pending: Path) -> None:
    old_target = os.readlink(link) if ctx.apply else ""
    ctx.plan(f"remove wrong symlink {link}")
    ctx.plan(f"rename staged link {pending} -> {link}")
    if not ctx.apply:
        return
    try:
        link.unlink()
        pending.rename(link)
    except OSError as exc:
        ctx.error(f"Failed to replace symlink {link} with staged link {pending}: {exc}")
        remove_pending_link(ctx, pending)
        if old_target:
            restore_symlink(ctx, link, old_target)


def replace_local_skill(ctx: Context, link: Path, pending: Path, backup: Path) -> None:
    ctx.plan(f"rename local skill {link} -> {backup}")
    ctx.plan(f"rename staged link {pending} -> {link}")
    if not ctx.apply:
        return
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        link.rename(backup)
        pending.rename(link)
    except OSError as exc:
        ctx.error(f"Failed to replace local skill {link} with staged link {pending}: {exc}")
        if (not link.exists() and not link.is_symlink()) and backup.exists():
            try:
                backup.rename(link)
            except OSError as rollback_exc:
                ctx.error(f"Failed to restore local skill {backup} -> {link}: {rollback_exc}")
        remove_pending_link(ctx, pending)


def path_exists_or_link(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def is_link_like_path(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not is_windows():
        return False
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def validate_no_conflicts(ctx: Context, root: Path, label: str) -> bool:
    conflicts = find_conflict_files(root)
    if not conflicts:
        return True
    for path in conflicts:
        ctx.error(f"Conflict-like file found in {label}: {path}")
    ctx.error(f"Resolve {label} conflicts before changing memory links.")
    return False


def copy_directory_tree(ctx: Context, source: Path, target: Path) -> bool:
    ctx.plan(f"copy directory {source} -> {target}")
    if not ctx.apply:
        return True
    try:
        shutil.copytree(source, target, symlinks=True)
    except OSError as exc:
        ctx.error(f"Failed to copy directory {source} -> {target}: {exc}")
        return False
    return True


def rename_path(ctx: Context, source: Path, target: Path, label: str) -> bool:
    ctx.plan(f"rename {label} {source} -> {target}")
    if not ctx.apply:
        return True
    try:
        source.rename(target)
    except OSError as exc:
        ctx.error(f"Failed to rename {source} -> {target}: {exc}")
        return False
    return True


def remove_path(ctx: Context, path: Path, label: str) -> bool:
    ctx.plan(f"remove {label} {path}")
    if not ctx.apply:
        return True
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        ctx.error(f"Failed to remove {path}: {exc}")
        return False
    return True


def next_unique_child(parent: Path, name: str) -> Path:
    candidate = parent / name
    if not candidate.exists() and not candidate.is_symlink():
        return candidate
    index = 1
    while True:
        numbered = parent / f"{name}-{index}"
        if not numbered.exists() and not numbered.is_symlink():
            return numbered
        index += 1


def iter_manifest_files(root: Path) -> Iterable[Path]:
    for current_root, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort(key=str.casefold)
        for filename in sorted(filenames, key=str.casefold):
            path = Path(current_root) / filename
            if path.name == MEMORIES_MANIFEST_NAME:
                continue
            yield path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_memories_manifest(root: Path, source_label: str) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for path in iter_manifest_files(root):
        relative = path.relative_to(root).as_posix()
        stat_result = path.stat()
        files[relative] = {
            "size": stat_result.st_size,
            "sha256": sha256_file(path),
        }
    return {
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": source_label,
        "files": files,
    }


def write_memories_manifest(ctx: Context, root: Path, manifest: dict[str, object]) -> bool:
    path = root / MEMORIES_MANIFEST_NAME
    ctx.plan(f"write memories manifest {path}")
    if not ctx.apply:
        return True
    try:
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        ctx.error(f"Failed to write memories manifest {path}: {exc}")
        return False
    return True


def validate_memories_manifest(ctx: Context, root: Path, label: str) -> bool:
    manifest_path = root / MEMORIES_MANIFEST_NAME
    if not manifest_path.is_file():
        ctx.error(f"{label} is missing required manifest: {manifest_path}")
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        ctx.error(f"Failed to read {label} manifest {manifest_path}: {exc}")
        return False
    files = manifest.get("files")
    if not isinstance(files, dict):
        ctx.error(f"{label} manifest has invalid files section: {manifest_path}")
        return False
    ok = True
    for relative, expected in sorted(files.items()):
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            ctx.error(f"{label} manifest contains unsafe relative path: {relative!r}")
            ok = False
            continue
        if not isinstance(expected, dict):
            ctx.error(f"{label} manifest entry is invalid for {relative}")
            ok = False
            continue
        path = root / relative
        if not path.is_file():
            ctx.error(f"{label} manifest file is missing: {path}")
            ok = False
            continue
        try:
            actual_size = path.stat().st_size
            actual_sha256 = sha256_file(path)
        except OSError as exc:
            ctx.error(f"Failed to inspect {label} manifest file {path}: {exc}")
            ok = False
            continue
        if expected.get("size") != actual_size:
            ctx.error(f"{label} manifest size mismatch for {path}")
            ok = False
        if expected.get("sha256") != actual_sha256:
            ctx.error(f"{label} manifest sha256 mismatch for {path}")
            ok = False
    return ok


def inspect_memories_manifest(root: Path) -> tuple[bool, dict[str, object] | None, list[str]]:
    manifest_path = root / MEMORIES_MANIFEST_NAME
    issues: list[str] = []
    if not manifest_path.is_file():
        return False, None, [f"missing manifest: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, None, [f"failed to read manifest {manifest_path}: {exc}"]
    if not isinstance(manifest, dict):
        return False, None, [f"manifest is not an object: {manifest_path}"]
    files = manifest.get("files")
    if not isinstance(files, dict):
        return False, manifest, [f"manifest has invalid files section: {manifest_path}"]
    for relative, expected in sorted(files.items()):
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            issues.append(f"manifest contains unsafe relative path: {relative!r}")
            continue
        if not isinstance(expected, dict):
            issues.append(f"manifest entry is invalid for {relative}")
            continue
        path = root / relative
        if not path.is_file():
            issues.append(f"manifest file is missing: {path}")
            continue
        try:
            actual_size = path.stat().st_size
            actual_sha256 = sha256_file(path)
        except OSError as exc:
            issues.append(f"failed to inspect manifest file {path}: {exc}")
            continue
        if expected.get("size") != actual_size:
            issues.append(f"manifest size mismatch for {path}")
        if expected.get("sha256") != actual_sha256:
            issues.append(f"manifest sha256 mismatch for {path}")
    return not issues, manifest, issues


def count_snapshot_dirs(path: Path) -> int:
    if not path.exists() or not path.is_dir():
        return 0
    count = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    for child in children:
        if child.is_dir() and not is_link_like_path(child):
            count += 1
    return count


def verify_memory_link(ctx: Context, link: Path, target: Path) -> bool:
    if not ctx.apply:
        return True
    if same_resolved_path(link, target):
        return True
    ctx.error(f"Memory link verification failed: {link} does not resolve to {target}")
    return False


def create_memory_layout(ctx: Context, local: Path, shared: Path) -> bool:
    if path_exists_or_link(local) and same_resolved_path(local, shared):
        ctx.info(f"Local memories already point to shared memories: {local} -> {shared}")
        return True
    if not create_directory_link(ctx, local, shared):
        return False
    return verify_memory_link(ctx, local, shared)


def warn_memory_key_files(ctx: Context, root: Path, label: str) -> None:
    for name in MEMORY_KEY_FILES:
        if (root / name).is_file():
            continue
        ctx.warn(f"{label} is missing common Codex memory file: {root / name}")


def build_codex_doctor_prompt(doctor_output: str, extra_prompt: str | None) -> str:
    parts = [
        "You are analyzing diagnostics from codex-shared-onboard.",
        "",
        "This is a read-only diagnostic pass. Do not modify files. Do not ask to run commands. "
        "Do not claim hidden state that is not present in the diagnostics. Clearly separate facts from inferences.",
        "",
        "Explain the current .codex/.codex-shared state from the doctor output below. Focus on:",
        "- errors that require action",
        "- warnings and whether they are expected",
        "- memory local/link/published state",
        "- shared skills linking state",
        "- Syncthing/tool availability",
        "- practical next steps",
        "",
        "Keep the answer concise.",
    ]
    if extra_prompt:
        parts.extend(["", "Additional user instructions:", extra_prompt])
    parts.extend(["", "Doctor output:", "```text", doctor_output.rstrip(), "```", ""])
    return "\n".join(parts)


def build_codex_doctor_command(
    args: argparse.Namespace,
    cwd: Path,
    codex_executable: str = "codex",
    output_path: Path | None = None,
) -> list[str]:
    command = [
        codex_executable,
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
    ]
    if output_path is not None:
        command.extend(["--output-last-message", str(output_path)])
    if args.codex_profile:
        command.extend(["-p", args.codex_profile])
    if args.codex_model:
        command.extend(["-m", args.codex_model])
    if args.codex_read_repo:
        command.extend(["-C", str(cwd)])
    command.append("-")
    return command


def extract_codex_failure_output(stdout: str) -> str:
    lines: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        lowered = stripped.casefold()
        is_codex_log = " error " in lowered and ("codex" in lowered or "api" in lowered)
        is_error_line = stripped.startswith(("ERROR", "[ERROR]"))
        is_network_error = any(
            marker in lowered
            for marker in (
                "http error",
                "unauthorized",
                "forbidden",
                "failed to connect",
                "stream disconnected",
                "error sending request",
            )
        )
        if is_codex_log or is_error_line or is_network_error:
            lines.append(line)
    if lines:
        return "\n".join(lines)
    tail = stdout.splitlines()[-20:]
    return "\n".join(tail)


def run_codex_doctor_analysis(ctx: Context, args: argparse.Namespace, doctor_output: str) -> int:
    codex_executable = tool_path("codex")
    if codex_executable is None:
        ctx.error("codex executable is missing; install Codex CLI or remove --codex.")
        return 1

    prompt = build_codex_doctor_prompt(doctor_output, args.codex_extra_prompt)
    output_file: Path | None = None
    try:
        output_fd, output_name = tempfile.mkstemp(prefix="codex-doctor-", suffix=".txt")
        os.close(output_fd)
        output_file = Path(output_name)
        command = build_codex_doctor_command(args, Path.cwd(), codex_executable, output_file)
        if ctx.verbose:
            ctx.info(f"run: {' '.join(shlex.quote(part) for part in command)}")
        result = subprocess.run(command, input=prompt, text=True, capture_output=True, check=False)
    except OSError as exc:
        ctx.error(f"Failed to run codex exec: {exc}")
        return 1

    try:
        if result.returncode == 0:
            final_message = ""
            if output_file and output_file.is_file():
                final_message = output_file.read_text(encoding="utf-8", errors="replace")
            if final_message:
                print(final_message, end="" if final_message.endswith("\n") else "\n")
            elif result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            return 0

        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        failure_output = extract_codex_failure_output(combined_output)
        if failure_output:
            print(failure_output, end="" if failure_output.endswith("\n") else "\n")
        ctx.error(f"codex exec failed with exit code {result.returncode}")
        return 1
    finally:
        if output_file is not None:
            try:
                output_file.unlink()
            except OSError:
                pass


def command_memories_adopt(ctx: Context) -> int:
    local = ctx.memories_dir
    shared = ctx.shared_memories_dir

    if path_exists_or_link(local) and same_resolved_path(local, shared):
        ctx.info(f"Local memories already point to shared memories: {local} -> {shared}")
        return 0

    if not local.exists() or not local.is_dir() or is_link_like_path(local):
        ctx.error(f"Adopt requires a real local memories directory: {local}")
        return 1
    if path_exists_or_link(shared):
        ctx.error(f"Adopt refuses to overwrite existing shared memories: {shared}")
        ctx.error("Use 'memories link' for reader machines, or resolve/archive shared memories manually first.")
        return 1
    if not validate_no_conflicts(ctx, local, "local memories"):
        return 1
    if not ensure_dir(ctx, ctx.shared_dir):
        return 1

    warn_memory_key_files(ctx, local, "local memories")
    backup = next_backup_path(local)
    if not copy_directory_tree(ctx, local, shared):
        return 1
    if not rename_path(ctx, local, backup, "local memories to backup"):
        return 1
    if not create_memory_layout(ctx, local, shared):
        if ctx.apply and not path_exists_or_link(local) and backup.exists():
            try:
                backup.rename(local)
            except OSError as exc:
                ctx.error(f"Failed to restore local memories backup {backup} -> {local}: {exc}")
        return 1
    return 1 if ctx.errors else 0


def command_memories_link(ctx: Context) -> int:
    local = ctx.memories_dir
    shared = ctx.shared_memories_dir

    if not shared.exists() or not shared.is_dir():
        ctx.error(f"Shared memories directory is missing: {shared}")
        ctx.error("Run 'memories adopt --apply' on the writer machine first.")
        return 1
    if not validate_no_conflicts(ctx, shared, "shared memories"):
        return 1
    if path_exists_or_link(local) and same_resolved_path(local, shared):
        ctx.info(f"Local memories already point to shared memories: {local} -> {shared}")
        return 0
    if not ensure_dir(ctx, ctx.codex_dir):
        return 1

    warn_memory_key_files(ctx, shared, "shared memories")
    if path_exists_or_link(local):
        if not local.is_dir():
            ctx.error(f"Local memories path exists but is not a replaceable real directory: {local}")
            return 1
        if not validate_no_conflicts(ctx, local, "local memories"):
            return 1
        backup = next_backup_path(local)
        if not rename_path(ctx, local, backup, "local memories to backup"):
            return 1
    if not create_memory_layout(ctx, local, shared):
        return 1
    return 1 if ctx.errors else 0


def command_memories_publish(ctx: Context) -> int:
    local = ctx.memories_dir
    current = ctx.published_current_dir

    if not local.exists() or not local.is_dir() or is_link_like_path(local):
        ctx.error(f"Publish requires a real local memories directory, not a link: {local}")
        return 1
    if not validate_no_conflicts(ctx, local, "local memories"):
        return 1
    if not ensure_dir(ctx, ctx.shared_dir):
        return 1
    if not ensure_dir(ctx, ctx.published_memories_dir):
        return 1
    if not ensure_dir(ctx, ctx.published_snapshots_dir):
        return 1

    warn_memory_key_files(ctx, local, "local memories")
    stamp = now_stamp()
    staging = next_unique_child(ctx.published_memories_dir, f".{MEMORIES_CURRENT_DIR_NAME}-staging-{stamp}")
    snapshot = next_unique_child(ctx.published_snapshots_dir, stamp)
    current_backup: Path | None = None

    if not copy_directory_tree(ctx, local, staging):
        return 1
    manifest_root = staging if ctx.apply else local
    manifest = build_memories_manifest(manifest_root, str(local))
    if not write_memories_manifest(ctx, staging, manifest):
        if path_exists_or_link(staging):
            remove_path(ctx, staging, "staged published memories")
        return 1
    if not copy_directory_tree(ctx, staging, snapshot):
        if path_exists_or_link(staging):
            remove_path(ctx, staging, "staged published memories")
        return 1
    if path_exists_or_link(current):
        current_backup = next_unique_child(ctx.published_memories_dir, f"{MEMORIES_CURRENT_DIR_NAME}.bak-publish-{stamp}")
        if not rename_path(ctx, current, current_backup, "previous published current"):
            if path_exists_or_link(staging):
                remove_path(ctx, staging, "staged published memories")
            return 1
    if not rename_path(ctx, staging, current, "published memories current"):
        if ctx.apply and current_backup is not None and path_exists_or_link(current_backup) and not path_exists_or_link(current):
            rename_path(ctx, current_backup, current, "previous published current rollback")
        if path_exists_or_link(staging):
            remove_path(ctx, staging, "staged published memories")
        return 1
    if current_backup is not None and path_exists_or_link(current_backup):
        if not remove_path(ctx, current_backup, "previous published current backup"):
            return 1
    return 1 if ctx.errors else 0


def command_memories_consume(ctx: Context) -> int:
    local = ctx.memories_dir
    current = ctx.published_current_dir

    if not current.exists() or not current.is_dir():
        ctx.error(f"Published memories current is missing: {current}")
        ctx.error("Run 'memories publish --apply' on the writer machine first.")
        return 1
    if not validate_memories_manifest(ctx, current, "published memories current"):
        return 1
    if not validate_no_conflicts(ctx, current, "published memories current"):
        return 1
    if path_exists_or_link(local):
        if not is_link_like_path(local) and not local.is_dir():
            ctx.error(f"Local memories path exists but is not a replaceable directory or link: {local}")
            return 1
        if not is_link_like_path(local) and not validate_no_conflicts(ctx, local, "local memories"):
            return 1
    if not ensure_dir(ctx, ctx.codex_dir):
        return 1

    warn_memory_key_files(ctx, current, "published memories current")
    stamp = now_stamp()
    staging = next_unique_child(ctx.codex_dir, f"memories.consume-staging-{stamp}")
    backup: Path | None = None

    if not copy_directory_tree(ctx, current, staging):
        return 1
    if ctx.apply and not validate_memories_manifest(ctx, staging, "staged consumed memories"):
        remove_path(ctx, staging, "staged consumed memories")
        return 1
    if path_exists_or_link(local):
        backup = next_backup_path(local)
        if not rename_path(ctx, local, backup, "local memories to backup"):
            if path_exists_or_link(staging):
                remove_path(ctx, staging, "staged consumed memories")
            return 1
    if not rename_path(ctx, staging, local, "consumed memories into local path"):
        if ctx.apply and backup is not None and path_exists_or_link(backup) and not path_exists_or_link(local):
            rename_path(ctx, backup, local, "local memories rollback")
        if path_exists_or_link(staging):
            remove_path(ctx, staging, "staged consumed memories")
        return 1
    if ctx.apply and is_link_like_path(local):
        ctx.error(f"Consume verification failed: local memories is still link-like: {local}")
        return 1
    return 1 if ctx.errors else 0


def link_shared_skills(ctx: Context) -> None:
    if not ensure_dir(ctx, ctx.codex_dir):
        return
    if not ensure_dir(ctx, ctx.skills_dir):
        return

    for target in iter_shared_skills(ctx):
        if ctx.errors:
            return
        link = ctx.skills_dir / target.name
        link_exists = link.exists() or link.is_symlink()
        if link_exists and same_resolved_path(link, target):
            if ctx.verbose:
                ctx.info(f"Already linked: {link} -> {target}")
            continue

        if not link_exists:
            create_directory_link(ctx, link, target)
            continue

        pending = next_pending_link_path(link)
        if not create_directory_link(ctx, pending, target):
            remove_pending_link(ctx, pending)
            continue

        if link.is_symlink():
            replace_wrong_symlink(ctx, link, pending)
        elif link_exists:
            backup = next_backup_path_in_dir(link, ctx.skills_backups_dir)
            replace_local_skill(ctx, link, pending, backup)


def prepare_shared_layout(ctx: Context) -> None:
    if not ensure_dir(ctx, ctx.shared_dir):
        return
    if not ensure_dir(ctx, ctx.shared_skills_dir):
        return
    if not ensure_dir(ctx, ctx.shared_dir / "tools"):
        return
    write_text_if_missing(ctx, ctx.shared_dir / ".stignore", STIGNORE_TEXT)
    write_text_if_missing(ctx, ctx.shared_dir / "memory-policy.md", MEMORY_POLICY_TEXT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Prepare Codex shared skills and diagnostics.",
        epilog=MAIN_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--codex-dir", type=Path, default=default_codex_dir())
    parser.add_argument("--shared-dir", type=Path, default=default_shared_dir())
    parser.add_argument("--apply", action="store_true", help="Actually change files. Default is dry-run.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--syncthing-url", default=DEFAULT_SYNCTHING_URL)
    parser.add_argument("--syncthing-api-key", default=None)

    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser(
        "install",
        help="Prepare shared folder and link shared user skills. Options: --apply, --configure-syncthing.",
        description="Prepare .codex-shared and link shared user skills into .codex/skills.",
        epilog=INSTALL_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    install.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    install.add_argument("--configure-syncthing", action="store_true", help="Try to add .codex-shared to the local Syncthing configuration.")
    doctor = sub.add_parser(
        "doctor",
        help="Diagnose shared Codex setup. Options: --codex, --codex-only, --codex-read-repo, --codex-profile, --codex-model, --codex-extra-prompt.",
        description="Diagnose .codex/.codex-shared links, memories, shared skills, Git, and Syncthing availability.",
        epilog=DOCTOR_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    doctor.add_argument("--codex", action="store_true", help="Ask Codex CLI to explain the captured diagnostics.")
    doctor.add_argument("--codex-only", action="store_true", help="Print only Codex analysis, suppressing raw doctor output.")
    doctor.add_argument("--codex-read-repo", action="store_true", help="Allow Codex analysis to read this repository in read-only mode.")
    doctor.add_argument("--codex-profile", default=None, help="Codex config profile to pass to 'codex exec'.")
    doctor.add_argument("--codex-model", default=None, help="Codex model to pass to 'codex exec'.")
    doctor.add_argument("--codex-extra-prompt", default=None, help="Extra instructions appended to the Codex analysis prompt.")
    snapshot = sub.add_parser(
        "snapshot",
        help="Create a local Git snapshot of .codex-shared. Options: --apply.",
        description="Create a local Git snapshot of .codex-shared for rollback/history.",
        epilog=SNAPSHOT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    snapshot.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    memories = sub.add_parser(
        "memories",
        help="Manage Codex memories sharing. Subcommands: adopt --apply, link --apply, publish --apply, consume --apply.",
        description="Manage Codex memories using legacy links or copy-based publish/consume.",
        epilog=MEMORIES_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    memories_sub = memories.add_subparsers(dest="memories_command", required=True)
    adopt = memories_sub.add_parser(
        "adopt",
        help="DEPRECATED: make the current local memories directory the linked shared source.",
        description="DEPRECATED: adopt the current real local .codex/memories directory as .codex-shared/memories.",
        epilog=MEMORIES_ADOPT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    adopt.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    link = memories_sub.add_parser(
        "link",
        help="DEPRECATED: link local memories to an existing shared memories directory.",
        description="DEPRECATED: link local .codex/memories to an existing .codex-shared/memories directory.",
        epilog=MEMORIES_LINK_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    link.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    publish = memories_sub.add_parser(
        "publish",
        help="Publish local memories into .codex-shared/memories-published/current.",
        description="Copy a real local .codex/memories directory into the shared published memories area.",
        epilog=MEMORIES_PUBLISH_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    publish.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    consume = memories_sub.add_parser(
        "consume",
        help="Consume published memories into a real local .codex/memories directory.",
        description="Replace local .codex/memories with a validated copy of shared published memories.",
        epilog=MEMORIES_CONSUME_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    consume.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    install_cli = sub.add_parser(
        "install-cli",
        help=f"Install a local '{APP_NAME}' launcher. Options: --apply, --bin-dir, --force, --no-path-update.",
        description=f"Install or update a local '{APP_NAME}' command launcher.",
        epilog=INSTALL_CLI_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    install_cli.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    install_cli.add_argument("--bin-dir", type=Path, default=default_bin_dir(), help="Directory where the launcher should be installed.")
    install_cli.add_argument("--force", action="store_true", help="Overwrite an existing launcher with different content.")
    install_cli.add_argument("--no-path-update", action="store_true", help="Do not add the launcher directory to PATH.")
    sub.add_parser("version", help="Print the tool version.")
    sub.add_parser("self-test", help="Run tests in temporary folders only.")
    return parser


def syncthing_config_candidates() -> list[Path]:
    if is_windows():
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return [Path(local_appdata) / "Syncthing" / "config.xml"]
        return []
    return [
        Path.home() / ".config" / "syncthing" / "config.xml",
        Path.home() / ".local" / "state" / "syncthing" / "config.xml",
    ]


def read_syncthing_api_key(ctx: Context | None = None) -> str | None:
    for path in syncthing_config_candidates():
        try:
            exists = path.exists()
        except OSError as exc:
            if ctx:
                ctx.warn(f"Could not inspect Syncthing config {path}: {exc}")
            continue
        if not exists:
            continue
        try:
            root = ET.parse(path).getroot()
        except OSError as exc:
            if ctx:
                ctx.warn(f"Could not read Syncthing config {path}: {exc}")
            continue
        except ET.ParseError as exc:
            if ctx:
                ctx.warn(f"Could not parse Syncthing config {path}: {exc}")
            continue
        gui = root.find("gui")
        if gui is None:
            continue
        api_key = gui.findtext("apikey")
        if api_key:
            return api_key.strip() or None
    return None


def syncthing_request(
    url: str,
    api_key: str,
    path: str,
    method: str = "GET",
    body: object | None = None,
) -> tuple[int, str]:
    base = url.rstrip("/")
    endpoint = path if path.startswith("/") else f"/{path}"
    data = None
    headers = {"X-API-Key": api_key}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base}{endpoint}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def syncthing_folder_path(shared_dir: Path) -> str:
    if not shared_dir.exists():
        return str(shared_dir)
    try:
        return str(shared_dir.resolve())
    except (OSError, RuntimeError):
        return str(shared_dir)


def paths_match(left: str, right: str) -> bool:
    left_text = os.path.normpath(left)
    right_text = os.path.normpath(right)
    if is_windows():
        left_text = os.path.normcase(left_text)
        right_text = os.path.normcase(right_text)
    return left_text == right_text


def configure_syncthing_folder(ctx: Context, url: str, api_key: str | None) -> None:
    if tool_path("syncthing") is None:
        ctx.warn("Syncthing binary is missing from PATH; trying the REST API anyway.")

    effective_api_key = api_key or read_syncthing_api_key(ctx)
    if not effective_api_key:
        ctx.warn("Syncthing API key is missing. Pass --syncthing-api-key or configure Syncthing first.")
        return

    try:
        status, text = syncthing_request(url, effective_api_key, "/rest/config/folders")
    except urllib.error.URLError as exc:
        ctx.warn(f"Could not contact Syncthing API at {url}: {exc}")
        return

    if status != 200:
        ctx.warn(f"Syncthing API returned HTTP {status} for GET /rest/config/folders.")
        return

    try:
        folders = json.loads(text)
    except json.JSONDecodeError as exc:
        ctx.warn(f"Syncthing API returned invalid JSON for GET /rest/config/folders: {exc}")
        return
    if not isinstance(folders, list):
        ctx.warn("Syncthing folders JSON has unexpected shape.")
        return

    folder_id = "codex-shared"
    folder_path = syncthing_folder_path(ctx.shared_dir)
    for folder in folders:
        if not isinstance(folder, dict) or folder.get("id") != folder_id:
            continue
        existing_path = folder.get("path")
        if isinstance(existing_path, str) and paths_match(existing_path, folder_path):
            ctx.info(f"Syncthing folder already configured: {folder_id} -> {folder_path}")
            return
        ctx.warn(f"Syncthing folder id '{folder_id}' already exists with a different path: {existing_path}")
        return

    new_folder = {
        "id": folder_id,
        "label": "Codex Shared",
        "path": folder_path,
        "type": "sendreceive",
        "devices": [],
        "rescanIntervalS": 3600,
        "fsWatcherEnabled": True,
        "ignorePerms": True,
    }
    ctx.plan(f"add Syncthing folder {folder_id} -> {folder_path}")
    if not ctx.apply:
        return

    try:
        post_status, _ = syncthing_request(
            url,
            effective_api_key,
            "/rest/config/folders",
            method="POST",
            body=new_folder,
        )
    except urllib.error.URLError as exc:
        ctx.warn(f"Could not add Syncthing folder through API at {url}: {exc}")
        return
    if post_status < 200 or post_status >= 300:
        ctx.warn(f"Syncthing API returned HTTP {post_status} for POST /rest/config/folders.")
        return
    ctx.info("Syncthing config updated. Remote devices still require UI approval.")


def command_install(ctx: Context, configure_syncthing: bool, args: argparse.Namespace) -> int:
    prepare_shared_layout(ctx)
    link_shared_skills(ctx)
    if configure_syncthing:
        configure_syncthing_folder(ctx, args.syncthing_url, args.syncthing_api_key)
    return 1 if ctx.errors else 0


def cli_launcher_name() -> str:
    return f"{APP_NAME}.cmd" if is_windows() else APP_NAME


def render_cli_launcher(script_path: Path, python_executable: Path | None = None) -> str:
    python_path = python_executable or Path(sys.executable)
    if is_windows():
        return f"@echo off\r\n\"{python_path}\" \"{script_path}\" %*\r\n"
    return f"#!/usr/bin/env sh\nexec {shlex.quote(str(python_path))} {shlex.quote(str(script_path))} \"$@\"\n"


def ensure_executable(ctx: Context, path: Path) -> None:
    if is_windows() or not ctx.apply:
        return
    try:
        current_mode = path.stat().st_mode
        path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        ctx.error(f"Failed to mark launcher executable {path}: {exc}")


def normalize_windows_path_entry(path: str) -> str:
    expanded = os.path.expandvars(path.strip().strip('"'))
    return ntpath.normcase(ntpath.normpath(expanded))


def windows_path_contains(path_value: str, directory: Path) -> bool:
    expected = normalize_windows_path_entry(str(directory))
    for entry in path_value.split(";"):
        if not entry.strip():
            continue
        if normalize_windows_path_entry(entry) == expected:
            return True
    return False


def append_windows_path(path_value: str, directory: Path) -> str:
    directory_text = str(directory)
    if not path_value.strip():
        return directory_text
    return f"{path_value.rstrip(';')};{directory_text}"


def broadcast_windows_environment_change(ctx: Context) -> None:
    if not ctx.apply:
        return
    try:
        import ctypes
    except ImportError as exc:
        ctx.warn(f"Could not notify Windows about PATH update: {exc}")
        return

    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    result = ctypes.c_ulong()
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            hwnd_broadcast,
            wm_settingchange,
            0,
            "Environment",
            smto_abortifhung,
            5000,
            ctypes.byref(result),
        )
    except (AttributeError, OSError) as exc:
        ctx.warn(f"Could not notify Windows about PATH update: {exc}")


def ensure_windows_user_path(ctx: Context, directory: Path) -> None:
    if not is_windows():
        return
    try:
        import winreg
    except ImportError as exc:
        ctx.error(f"Could not update Windows PATH because winreg is unavailable: {exc}")
        return

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_PATH_REGISTRY_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            try:
                current_value, value_type = winreg.QueryValueEx(key, WINDOWS_PATH_VALUE_NAME)
            except FileNotFoundError:
                current_value, value_type = "", winreg.REG_EXPAND_SZ
            if not isinstance(current_value, str):
                ctx.error(f"Windows user PATH registry value has unexpected type: {type(current_value).__name__}")
                return
            if windows_path_contains(current_value, directory):
                ctx.info(f"Windows user PATH already contains: {directory}")
                return
            updated_value = append_windows_path(current_value, directory)
            ctx.plan(f"add {directory} to Windows user PATH")
            if ctx.apply:
                if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                    value_type = winreg.REG_EXPAND_SZ
                winreg.SetValueEx(key, WINDOWS_PATH_VALUE_NAME, 0, value_type, updated_value)
                broadcast_windows_environment_change(ctx)
                ctx.info("Windows user PATH updated. Open a new terminal before running the command.")
    except OSError as exc:
        ctx.error(f"Failed to update Windows user PATH: {exc}")


def command_install_cli(ctx: Context, args: argparse.Namespace) -> int:
    script_path = Path(__file__).resolve()
    if not script_path.is_file():
        ctx.error(f"Cannot find source script: {script_path}")
        return 1

    bin_dir = args.bin_dir.expanduser()
    launcher = bin_dir / cli_launcher_name()
    expected_text = render_cli_launcher(script_path)

    if launcher.exists() or launcher.is_symlink():
        if launcher.is_dir() and not launcher.is_symlink():
            ctx.error(f"Launcher path exists as a directory: {launcher}")
            return 1
        try:
            current_text = launcher.read_text(encoding="utf-8")
        except OSError as exc:
            ctx.error(f"Failed to read existing launcher {launcher}: {exc}")
            return 1
        if current_text == expected_text:
            ctx.info(f"CLI launcher already installed: {launcher}")
            ensure_executable(ctx, launcher)
            return 1 if ctx.errors else 0
        if not args.force:
            ctx.error(f"Launcher already exists with different content: {launcher}. Re-run with --force to overwrite it.")
            return 1

    if not ensure_dir(ctx, bin_dir):
        return 1
    ctx.plan(f"write CLI launcher {launcher} -> {script_path}")
    if ctx.apply:
        try:
            launcher.write_text(expected_text, encoding="utf-8", newline="")
        except OSError as exc:
            ctx.error(f"Failed to write CLI launcher {launcher}: {exc}")
            return 1
        ensure_executable(ctx, launcher)
    if is_windows() and not args.no_path_update:
        ensure_windows_user_path(ctx, bin_dir)
    elif not is_windows():
        ctx.info(f"Make sure {bin_dir} is on PATH before running {APP_NAME}.")
    return 1 if ctx.errors else 0


def find_conflict_files(root: Path) -> list[Path]:
    def is_conflict_filename(filename: str) -> bool:
        name = filename.casefold()
        if "sync-conflict" in name or "sync_conflict" in name:
            return True
        if "conflicted copy" in name or "conflicted-copy" in name:
            return True
        return bool(re.search(r"(^|[._ -])conflicted?([._ -]|$)", name))

    def is_link_like_dir(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            attrs = getattr(os.lstat(path), "st_file_attributes", 0)
        except OSError:
            return True
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

    if not root.exists() or not root.is_dir() or is_link_like_dir(root):
        return []
    conflicts = []
    for current_root, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current_root)

        kept_dirs = []
        for dirname in dirnames:
            path = current_path / dirname
            if is_link_like_dir(path):
                continue
            try:
                if not path.is_dir():
                    continue
            except OSError:
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            if not is_conflict_filename(filename):
                continue
            path = current_path / filename
            try:
                path.stat()
            except OSError:
                continue
            conflicts.append(path)
    return sorted(conflicts, key=lambda item: str(item).casefold())


def find_active_shared_conflict_files(shared_dir: Path) -> list[Path]:
    archive_dir = shared_dir / "archive"
    active_conflicts = []
    for path in find_conflict_files(shared_dir):
        try:
            path.relative_to(archive_dir)
            continue
        except ValueError:
            active_conflicts.append(path)
    return active_conflicts


def tool_path(name: str) -> str | None:
    return shutil.which(name)


def print_memory_diagnostics(ctx: Context) -> None:
    local_memories = ctx.memories_dir
    shared_memories = ctx.shared_memories_dir
    published_current = ctx.published_current_dir

    if not path_exists_or_link(local_memories):
        print(f"memories_local=missing path={local_memories}")
        ctx.warn(f"Local memories path is missing: {local_memories}")
    elif shared_memories.exists() and same_resolved_path(local_memories, shared_memories):
        print(f"memories_local=legacy-linked local={local_memories} shared={shared_memories}")
        ctx.warn("Legacy memories link is deprecated; prefer memories publish/consume.")
        if not is_windows() and is_link_like_path(local_memories):
            ctx.warn("WSL/Linux whole-directory memory symlink preserves Codex internals but may trigger sandbox issues.")
    elif is_link_like_path(local_memories):
        print(f"memories_local=link local={local_memories}")
        ctx.warn("Local memories is link-like but does not resolve to legacy shared memories.")
    elif local_memories.is_dir():
        print(f"memories_local=real path={local_memories}")
        if shared_memories.exists() and not published_current.exists():
            ctx.warn(f"Local memories are not linked to legacy shared memories: {local_memories}")
    else:
        print(f"memories_local=invalid path={local_memories}")
        ctx.warn(f"Local memories path exists but is not a directory: {local_memories}")

    if published_current.exists() and published_current.is_dir():
        valid, manifest, issues = inspect_memories_manifest(published_current)
        if valid:
            files = manifest.get("files", {}) if manifest else {}
            created_at = manifest.get("created_at", "unknown") if manifest else "unknown"
            source = manifest.get("source", "unknown") if manifest else "unknown"
            print(f"memories_published=valid current={published_current} files={len(files)} created_at={created_at} source={source}")
        else:
            print(f"memories_published=invalid current={published_current}")
            for issue in issues:
                ctx.warn(f"Published memories manifest issue: {issue}")
    elif ctx.published_memories_dir.exists():
        print(f"memories_published=missing current={published_current}")
        ctx.warn(f"Published memories current is missing: {published_current}")
    else:
        print(f"memories_published=missing current={published_current}")

    snapshots = count_snapshot_dirs(ctx.published_snapshots_dir)
    print(f"memories_published_snapshots={snapshots} path={ctx.published_snapshots_dir}")


def print_doctor_diagnostics(ctx: Context) -> int:
    print(f"platform={platform.platform()}")
    print(f"is_windows={is_windows()}")
    print(f"is_wsl={is_wsl()}")
    print(f"codex_dir={ctx.codex_dir}")
    print(f"shared_dir={ctx.shared_dir}")

    if not ctx.codex_dir.exists():
        ctx.warn(f"Codex directory is missing: {ctx.codex_dir}")
    elif not ctx.codex_dir.is_dir():
        ctx.error(f"Expected codex directory but found file: {ctx.codex_dir}")

    if not ctx.shared_dir.exists():
        ctx.warn(f"Shared directory is missing: {ctx.shared_dir}")
    elif not ctx.shared_dir.is_dir():
        ctx.error(f"Expected shared directory but found file: {ctx.shared_dir}")

    for required_file in (".stignore", "memory-policy.md"):
        path = ctx.shared_dir / required_file
        if not path.is_file():
            ctx.warn(f"Missing shared file: {path}")

    for path in find_active_shared_conflict_files(ctx.shared_dir):
        ctx.warn(f"Conflict-like file found: {path}")

    print(f"python={sys.version.split()[0]} ({sys.executable})")
    git = tool_path("git")
    syncthing = tool_path("syncthing")
    print(f"git={git if git else 'missing'}")
    print(f"syncthing={syncthing if syncthing else 'missing'}")

    print_memory_diagnostics(ctx)

    for target in iter_shared_skills(ctx):
        local = ctx.skills_dir / target.name
        local_exists = local.exists() or local.is_symlink()
        if local_exists and same_resolved_path(local, target):
            print(f"skill {target.name}=linked local={local} shared={target}")
        elif local_exists:
            ctx.warn(f"Local skill exists but is different: {local} (shared: {target})")
        else:
            ctx.warn(f"Shared skill is not linked locally: {target} -> {local}")

    return 1 if ctx.errors else 0


def command_doctor(ctx: Context, args: argparse.Namespace) -> int:
    if not args.codex:
        return print_doctor_diagnostics(ctx)

    doctor_stdout = io.StringIO()
    with contextlib.redirect_stdout(doctor_stdout):
        doctor_code = print_doctor_diagnostics(ctx)
    doctor_output = doctor_stdout.getvalue()

    if not args.codex_only:
        print(doctor_output, end="")
        if doctor_output and not doctor_output.endswith("\n"):
            print()
        print()
        print(CODEX_ANALYSIS_SEPARATOR)

    codex_code = run_codex_doctor_analysis(ctx, args, doctor_output)
    return 1 if doctor_code or codex_code else 0


def command_version() -> int:
    print(f"{APP_NAME} {APP_VERSION}")
    return 0


def run_checked(ctx: Context, command: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    if ctx.verbose:
        ctx.info(f"run cwd={cwd}: {' '.join(command)}")
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        ctx.error(f"Failed to run {' '.join(command)} in {cwd}: {exc}")
        return None


def command_snapshot(ctx: Context) -> int:
    if tool_path("git") is None:
        ctx.warn("Git binary is missing from PATH; skipping local snapshot.")
        return 0

    if not ensure_dir(ctx, ctx.shared_dir):
        return 1 if ctx.errors else 0

    git_dir = ctx.shared_dir / ".git"
    if not git_dir.exists():
        ctx.plan(f"initialize Git repo in {ctx.shared_dir}")
        if ctx.apply:
            result = run_checked(ctx, ["git", "init"], ctx.shared_dir)
            if result is None:
                return 1
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                ctx.error(f"Failed to initialize Git repo in {ctx.shared_dir}: {detail}")
                return 1

    ctx.plan(f"stage shared Codex files in {ctx.shared_dir}")
    if ctx.apply:
        add_result = run_checked(ctx, ["git", "add", "."], ctx.shared_dir)
        if add_result is None:
            return 1
        if add_result.returncode != 0:
            detail = (add_result.stderr or add_result.stdout).strip()
            ctx.error(f"Failed to stage shared Codex files in {ctx.shared_dir}: {detail}")
            return 1

        status_result = run_checked(ctx, ["git", "status", "--porcelain"], ctx.shared_dir)
        if status_result is None:
            return 1
        if status_result.returncode != 0:
            detail = (status_result.stderr or status_result.stdout).strip()
            ctx.error(f"Failed to inspect Git status in {ctx.shared_dir}: {detail}")
            return 1
        if not status_result.stdout.strip():
            ctx.info("No snapshot changes to commit.")
            return 1 if ctx.errors else 0

        timestamp = dt.datetime.now().isoformat(timespec="seconds")
        commit_result = run_checked(
            ctx,
            ["git", "commit", "-m", f"snapshot: codex shared layer {timestamp}"],
            ctx.shared_dir,
        )
        if commit_result is None:
            return 1
        if commit_result.returncode != 0:
            detail = (commit_result.stderr or commit_result.stdout).strip()
            ctx.error(f"Failed to commit shared Codex snapshot in {ctx.shared_dir}: {detail}")

    return 1 if ctx.errors else 0


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_script(args: list[str]) -> int:
    return main(args)


def safe_cleanup_self_test_case(case_dir: Path, base_dir: Path) -> None:
    marker = case_dir / ".codex-onboard-self-test-marker"
    if not marker.is_file():
        print(f"[WARN] self-test cleanup skipped unmarked directory: {case_dir}")
        return

    def retry_remove(func: object, path: str, _exc_info: object) -> None:
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)  # type: ignore[operator]
        except OSError:
            raise

    try:
        shutil.rmtree(case_dir, onerror=retry_remove)
    except OSError as exc:
        print(f"[WARN] self-test cleanup failed for {case_dir}: {exc}")
        return

    try:
        base_dir.rmdir()
    except OSError:
        pass


def command_self_test() -> int:
    base = Path.cwd() / ".onboard-test-tmp"
    case_prefix = f"case-{now_stamp()}-{os.getpid()}"
    case_dir = base / case_prefix
    index = 1
    while case_dir.exists():
        index += 1
        case_dir = base / f"{case_prefix}-{index}"

    codex_dir = case_dir / ".codex"
    shared_dir = case_dir / ".codex-shared"
    shared_skill = shared_dir / "skills-user" / "demo-skill"
    local_skill = codex_dir / "skills" / "demo-skill"
    marker = case_dir / ".codex-onboard-self-test-marker"

    try:
        base.mkdir(exist_ok=True)
        case_dir.mkdir()
        marker.write_text("owned by codex_shared_onboard.py self-test\n", encoding="utf-8", newline="\n")

        dry_run_code = run_script(
            [
                "--codex-dir",
                str(codex_dir),
                "--shared-dir",
                str(shared_dir),
                "install",
            ]
        )
        assert_true(dry_run_code == 0, "dry-run install should return 0")
        assert_true(not shared_dir.exists(), "dry-run install should not create shared dir")

        shared_skill.mkdir(parents=True)
        (shared_skill / "SKILL.md").write_text("# Shared demo skill\n", encoding="utf-8", newline="\n")
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text("# Local demo skill\n", encoding="utf-8", newline="\n")

        apply_code = run_script(
            [
                "--codex-dir",
                str(codex_dir),
                "--shared-dir",
                str(shared_dir),
                "install",
                "--apply",
            ]
        )
        assert_true(apply_code == 0, "apply install should return 0")
        assert_true((shared_dir / ".stignore").is_file(), "apply install should create .stignore")
        assert_true((shared_dir / "memory-policy.md").is_file(), "apply install should create memory-policy.md")

        backups = sorted((codex_dir / "skills-backups").glob("demo-skill.bak-local-*"))
        assert_true(len(backups) == 1, "apply install should backup existing local demo-skill")
        assert_true((backups[0] / "SKILL.md").read_text(encoding="utf-8") == "# Local demo skill\n", "backup should keep local skill")
        active_skill_backups = sorted((codex_dir / "skills").glob("demo-skill.bak-local-*"))
        assert_true(not active_skill_backups, "skill backups should not stay under active skills dir")
        assert_true((local_skill / "SKILL.md").read_text(encoding="utf-8") == "# Shared demo skill\n", "local skill should expose shared SKILL.md")
        assert_true(same_resolved_path(local_skill, shared_skill), "local demo-skill should resolve to shared skill")

        cli_bin_dir = case_dir / "bin"
        cli_launcher = cli_bin_dir / cli_launcher_name()
        cli_dry_run_code = run_script(
            [
                "install-cli",
                "--bin-dir",
                str(cli_bin_dir),
                "--no-path-update",
            ]
        )
        assert_true(cli_dry_run_code == 0, "dry-run install-cli should return 0")
        assert_true(not cli_launcher.exists(), "dry-run install-cli should not create launcher")

        cli_apply_code = run_script(
            [
                "install-cli",
                "--bin-dir",
                str(cli_bin_dir),
                "--no-path-update",
                "--apply",
            ]
        )
        assert_true(cli_apply_code == 0, "apply install-cli should return 0")
        assert_true(cli_launcher.is_file(), "install-cli should create launcher")
        assert_true(windows_path_contains("C:\\Tools;C:\\Users\\Admin\\.local\\bin", Path("C:/Users/Admin/.local/bin")), "windows_path_contains should match normalized paths")
        assert_true(append_windows_path("C:\\Tools;", Path("C:/Users/Admin/.local/bin")) == f"C:\\Tools;{Path('C:/Users/Admin/.local/bin')}", "append_windows_path should append without duplicate separator")
        script_path = Path(__file__).resolve()
        version_result = subprocess.run(
            [sys.executable, str(script_path), "--version"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(version_result.returncode == 0, "--version should return 0")
        assert_true(version_result.stdout.strip() == f"{APP_NAME} {APP_VERSION}", "--version should print app version")
        version_command_result = subprocess.run(
            [sys.executable, str(script_path), "version"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(version_command_result.returncode == 0, "version command should return 0")
        assert_true(version_command_result.stdout.strip() == f"{APP_NAME} {APP_VERSION}", "version command should print app version")
        root_help_result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(root_help_result.returncode == 0, "root --help should return 0")
        root_help_compact = " ".join(root_help_result.stdout.split())
        assert_true("command reference:" not in root_help_result.stdout, "root --help should not duplicate argparse sections")
        assert_true("Options: --apply, --configure-syncthing" in root_help_compact, "root --help should show install options in command list")
        assert_true("Options: --codex, --codex-only" in root_help_compact, "root --help should show doctor options in command list")
        assert_true(
            "Subcommands: adopt --apply, link --apply, publish --apply, consume --apply" in root_help_compact,
            "root --help should show memories subcommands in command list",
        )
        assert_true("install-cli Install a local" in root_help_compact, "root --help should show install-cli command")
        assert_true("--bin-dir" in root_help_compact and "--force" in root_help_compact, "root --help should show install-cli options in command list")
        doctor_help_result = subprocess.run(
            [sys.executable, str(script_path), "doctor", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(doctor_help_result.returncode == 0, "doctor --help should return 0")
        assert_true("Codex analysis behavior:" in doctor_help_result.stdout, "doctor --help should include Codex behavior reference")
        assert_true("--codex-read-repo" in doctor_help_result.stdout, "doctor --help should show read-repo option")
        install_help_result = subprocess.run(
            [sys.executable, str(script_path), "install", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(install_help_result.returncode == 0, "install --help should return 0")
        assert_true("--configure-syncthing" in install_help_result.stdout, "install --help should show Syncthing option")
        assert_true("Install behavior:" in install_help_result.stdout, "install --help should include behavior reference")
        memories_help_result = subprocess.run(
            [sys.executable, str(script_path), "memories", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(memories_help_result.returncode == 0, "memories --help should return 0")
        assert_true("Memory sharing commands:" in memories_help_result.stdout, "memories --help should include subcommand reference")
        assert_true("DEPRECATED" in memories_help_result.stdout, "memories --help should mark adopt/link as deprecated")
        adopt_help_result = subprocess.run(
            [sys.executable, str(script_path), "memories", "adopt", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(adopt_help_result.returncode == 0, "memories adopt --help should return 0")
        assert_true("DEPRECATED" in adopt_help_result.stdout, "memories adopt --help should mark adopt as deprecated")
        link_help_result = subprocess.run(
            [sys.executable, str(script_path), "memories", "link", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(link_help_result.returncode == 0, "memories link --help should return 0")
        assert_true("DEPRECATED" in link_help_result.stdout, "memories link --help should mark link as deprecated")
        install_cli_help_result = subprocess.run(
            [sys.executable, str(script_path), "install-cli", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(install_cli_help_result.returncode == 0, "install-cli --help should return 0")
        assert_true("--no-path-update" in install_cli_help_result.stdout, "install-cli --help should show PATH option")
        assert_true("Launcher behavior:" in install_cli_help_result.stdout, "install-cli --help should include launcher behavior")
        if not is_windows():
            assert_true(os.access(cli_launcher, os.X_OK), "install-cli launcher should be executable")
            help_result = subprocess.run(
                [str(cli_launcher), "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            assert_true(help_result.returncode == 0, "installed launcher should run --help")
            assert_true(APP_NAME in help_result.stdout, "installed launcher help should mention app name")

        memories_dir = shared_dir / "memories"
        memories_dir.mkdir()
        conflict_file = memories_dir / "MEMORY.sync-conflict-test.md"
        conflict_file.write_text("conflict\n", encoding="utf-8", newline="\n")
        false_conflict_file = memories_dir / "branch_conflicts.md"
        false_conflict_file.write_text("ordinary memory note\n", encoding="utf-8", newline="\n")
        archived_conflict_file = shared_dir / "archive" / "memory-conflicts" / "MEMORY.sync-conflict-archived.md"
        archived_conflict_file.parent.mkdir(parents=True)
        archived_conflict_file.write_text("archived conflict\n", encoding="utf-8", newline="\n")
        assert_true(false_conflict_file not in find_conflict_files(shared_dir), "ordinary files mentioning conflicts should not be treated as conflict files")

        fake_bin = case_dir / "fake-bin"
        fake_bin.mkdir()
        fake_codex_log = case_dir / "fake-codex-log.json"
        fake_codex_stdin = case_dir / "fake-codex-stdin.txt"
        fake_codex_fail = case_dir / "fake-codex-fail"
        fake_codex_script = fake_bin / "fake_codex.py"
        fake_codex_script.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "from pathlib import Path",
                    f"log_path = Path({str(fake_codex_log)!r})",
                    f"stdin_path = Path({str(fake_codex_stdin)!r})",
                    f"fail_path = Path({str(fake_codex_fail)!r})",
                    "argv = sys.argv[1:]",
                    "stdin_text = sys.stdin.read()",
                    "stdin_path.write_text(stdin_text, encoding='utf-8', newline='\\n')",
                    "log_path.write_text(json.dumps({'argv': argv}, ensure_ascii=False), encoding='utf-8', newline='\\n')",
                    "if fail_path.exists():",
                    "    print('SECRET PROMPT SHOULD NOT PRINT')",
                    "    print('ERROR: fake failure transcript line')",
                    "    print('SECRET STDERR SHOULD NOT PRINT', file=sys.stderr)",
                    "    print('ERROR: FAKE CODEX FAILURE', file=sys.stderr)",
                    "    raise SystemExit(7)",
                    "analysis = 'FAKE CODEX ANALYSIS\\n'",
                    "for index, value in enumerate(argv[:-1]):",
                    "    if value in ('-o', '--output-last-message'):",
                    "        Path(argv[index + 1]).write_text(analysis, encoding='utf-8', newline='\\n')",
                    "        break",
                    "print('FAKE CODEX TRANSCRIPT')",
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if is_windows():
            fake_codex = fake_bin / "codex.cmd"
            fake_codex.write_text(
                f'@echo off\r\n"{sys.executable}" "{fake_codex_script}" %*\r\n',
                encoding="utf-8",
                newline="",
            )
        else:
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                f"#!{sys.executable}\n"
                f"exec(open({str(fake_codex_script)!r}, encoding='utf-8').read())\n",
                encoding="utf-8",
                newline="\n",
            )
            fake_codex.chmod(0o755)

        def run_script_with_fake_codex(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            return subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), *arguments],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

        doctor_stdout = io.StringIO()
        with contextlib.redirect_stdout(doctor_stdout):
            doctor_code = run_script(
                [
                    "--codex-dir",
                    str(codex_dir),
                    "--shared-dir",
                    str(shared_dir),
                    "doctor",
                ]
            )
        doctor_output = doctor_stdout.getvalue()
        assert_true(doctor_code == 0, "doctor should return 0 for warning-only diagnostics")
        assert_true(
            conflict_file.name in doctor_output or "conflict" in doctor_output.casefold(),
            "doctor should report the conflict file",
        )
        assert_true(archived_conflict_file.name not in doctor_output, "doctor should ignore archived conflict files")

        codex_result = run_script_with_fake_codex(
            [
                "--codex-dir",
                str(codex_dir),
                "--shared-dir",
                str(shared_dir),
                "doctor",
                "--codex",
                "--codex-extra-prompt",
                "Answer in Russian.",
            ]
        )
        assert_true(
            codex_result.returncode == 0,
            f"doctor --codex should return 0 with fake Codex, got {codex_result.returncode}; stdout={codex_result.stdout!r}; stderr={codex_result.stderr!r}",
        )
        assert_true(CODEX_ANALYSIS_SEPARATOR in codex_result.stdout, "doctor --codex should print analysis separator")
        assert_true("FAKE CODEX ANALYSIS" in codex_result.stdout, "doctor --codex should print fake Codex output")
        assert_true("FAKE CODEX TRANSCRIPT" not in codex_result.stdout, "doctor --codex should print final Codex message, not transcript stdout")
        assert_true("platform=" in codex_result.stdout, "doctor --codex should print raw doctor output by default")

        fake_argv = json.loads(fake_codex_log.read_text(encoding="utf-8"))["argv"]
        assert_true(fake_argv[:4] == ["--ask-for-approval", "never", "exec", "--ephemeral"], "doctor --codex should use codex exec --ephemeral with global approval policy")
        assert_true("--sandbox" in fake_argv and "read-only" in fake_argv, "doctor --codex should use read-only sandbox")
        assert_true("--color" in fake_argv and "never" in fake_argv, "doctor --codex should disable Codex output color")
        assert_true("--output-last-message" in fake_argv, "doctor --codex should request final message output")
        assert_true("--ask-for-approval" in fake_argv and "never" in fake_argv, "doctor --codex should never request approval")
        assert_true(fake_argv[-1] == "-", "doctor --codex should pass prompt through stdin")
        fake_stdin = fake_codex_stdin.read_text(encoding="utf-8")
        assert_true("Doctor output:" in fake_stdin, "doctor --codex prompt should include doctor output section")
        assert_true("Answer in Russian." in fake_stdin, "doctor --codex prompt should include extra prompt")
        assert_true("platform=" in fake_stdin, "doctor --codex prompt should include captured diagnostics")

        codex_only_result = run_script_with_fake_codex(
            [
                "--codex-dir",
                str(codex_dir),
                "--shared-dir",
                str(shared_dir),
                "doctor",
                "--codex",
                "--codex-only",
            ]
        )
        assert_true(codex_only_result.returncode == 0, "doctor --codex-only should return 0 with fake Codex")
        assert_true("FAKE CODEX ANALYSIS" in codex_only_result.stdout, "doctor --codex-only should print analysis")
        assert_true("platform=" not in codex_only_result.stdout, "doctor --codex-only should suppress raw doctor output")

        codex_options_result = run_script_with_fake_codex(
            [
                "--codex-dir",
                str(codex_dir),
                "--shared-dir",
                str(shared_dir),
                "doctor",
                "--codex",
                "--codex-only",
                "--codex-read-repo",
                "--codex-profile",
                "profile-test",
                "--codex-model",
                "model-test",
            ]
        )
        assert_true(codex_options_result.returncode == 0, "doctor --codex should pass optional Codex flags")
        fake_argv = json.loads(fake_codex_log.read_text(encoding="utf-8"))["argv"]
        assert_true("-C" in fake_argv and str(Path.cwd()) in fake_argv, "doctor --codex-read-repo should pass repository root")
        assert_true("-p" in fake_argv and "profile-test" in fake_argv, "doctor --codex-profile should pass profile")
        assert_true("-m" in fake_argv and "model-test" in fake_argv, "doctor --codex-model should pass model")

        fake_codex_fail.write_text("fail\n", encoding="utf-8", newline="\n")
        failing_codex_result = run_script_with_fake_codex(
            [
                "--codex-dir",
                str(codex_dir),
                "--shared-dir",
                str(shared_dir),
                "doctor",
                "--codex",
                "--codex-only",
            ]
        )
        assert_true(failing_codex_result.returncode != 0, "doctor --codex should fail when codex exec fails")
        assert_true("codex exec failed with exit code" in failing_codex_result.stdout, "doctor --codex should report Codex exit code")
        assert_true("ERROR: fake failure transcript line" in failing_codex_result.stdout, "doctor --codex should keep error-like Codex stdout lines")
        assert_true("ERROR: FAKE CODEX FAILURE" in failing_codex_result.stdout, "doctor --codex should keep error-like Codex stderr lines")
        assert_true("SECRET PROMPT SHOULD NOT PRINT" not in failing_codex_result.stdout, "doctor --codex should not print full failed Codex transcript")
        assert_true("SECRET STDERR SHOULD NOT PRINT" not in failing_codex_result.stdout, "doctor --codex should not print full failed Codex stderr transcript")
        fake_codex_fail.unlink()

        publish_case = case_dir / "memories-publish-consume"
        writer_codex = publish_case / "writer" / ".codex"
        reader_codex = publish_case / "reader" / ".codex"
        published_shared = publish_case / ".codex-shared"
        writer_memories = writer_codex / "memories"
        reader_memories = reader_codex / "memories"
        published_current = published_shared / "memories-published" / "current"
        published_snapshots = published_shared / "memories-published" / "snapshots"
        writer_memories.mkdir(parents=True)
        (writer_memories / ".git").mkdir()
        (writer_memories / ".agents").mkdir()
        (writer_memories / ".codex").write_text("writer internal marker\n", encoding="utf-8", newline="\n")
        (writer_memories / "MEMORY.md").write_text("writer aggregate\n", encoding="utf-8", newline="\n")
        (writer_memories / "memory_summary.md").write_text("writer summary\n", encoding="utf-8", newline="\n")
        (writer_memories / "raw_memories.md").write_text("writer raw\n", encoding="utf-8", newline="\n")
        (writer_memories / "rollout_summaries").mkdir()
        (writer_memories / "rollout_summaries" / "demo.jsonl").write_text("writer rollout\n", encoding="utf-8", newline="\n")

        publish_dry_run_code = run_script(
            [
                "--codex-dir",
                str(writer_codex),
                "--shared-dir",
                str(published_shared),
                "memories",
                "publish",
            ]
        )
        assert_true(publish_dry_run_code == 0, "dry-run memories publish should return 0")
        assert_true(not published_current.exists(), "dry-run memories publish should not create published current")

        publish_apply_code = run_script(
            [
                "--codex-dir",
                str(writer_codex),
                "--shared-dir",
                str(published_shared),
                "memories",
                "publish",
                "--apply",
            ]
        )
        assert_true(publish_apply_code == 0, "apply memories publish should return 0")
        assert_true((published_current / "MEMORY.md").read_text(encoding="utf-8") == "writer aggregate\n", "publish should copy writer memories into current")
        assert_true((published_current / ".git").is_dir(), "publish should include Codex-owned .git internals")
        assert_true((published_current / ".agents").is_dir(), "publish should include Codex-owned .agents internals")
        assert_true((published_current / ".codex").is_file(), "publish should include Codex-owned .codex internals")
        assert_true((published_current / "manifest.json").is_file(), "publish should write a current manifest")
        published_manifest = json.loads((published_current / "manifest.json").read_text(encoding="utf-8"))
        assert_true("MEMORY.md" in published_manifest["files"], "publish manifest should list MEMORY.md")
        assert_true("manifest.json" not in published_manifest["files"], "publish manifest should not list itself")
        assert_true(len(list(published_snapshots.iterdir())) == 1, "publish should create one immutable snapshot")
        writer_doctor_stdout = io.StringIO()
        with contextlib.redirect_stdout(writer_doctor_stdout):
            writer_doctor_code = run_script(
                [
                    "--codex-dir",
                    str(writer_codex),
                    "--shared-dir",
                    str(published_shared),
                    "doctor",
                ]
            )
        writer_doctor_output = writer_doctor_stdout.getvalue()
        assert_true(writer_doctor_code == 0, "doctor should return 0 for copy-based writer diagnostics")
        assert_true("memories_local=real" in writer_doctor_output, "doctor should report real local memories")
        assert_true("memories_published=valid" in writer_doctor_output, "doctor should report valid published memories")
        assert_true("memories_published_snapshots=1" in writer_doctor_output, "doctor should report published snapshot count")

        reader_memories.mkdir(parents=True)
        (reader_memories / "MEMORY.md").write_text("stale reader aggregate\n", encoding="utf-8", newline="\n")
        consume_dry_run_code = run_script(
            [
                "--codex-dir",
                str(reader_codex),
                "--shared-dir",
                str(published_shared),
                "memories",
                "consume",
            ]
        )
        assert_true(consume_dry_run_code == 0, "dry-run memories consume should return 0")
        assert_true((reader_memories / "MEMORY.md").read_text(encoding="utf-8") == "stale reader aggregate\n", "dry-run memories consume should not replace local memories")

        consume_apply_code = run_script(
            [
                "--codex-dir",
                str(reader_codex),
                "--shared-dir",
                str(published_shared),
                "memories",
                "consume",
                "--apply",
            ]
        )
        assert_true(consume_apply_code == 0, "apply memories consume should return 0")
        assert_true((reader_memories / "MEMORY.md").read_text(encoding="utf-8") == "writer aggregate\n", "consume should copy published memories into local memories")
        assert_true(not is_link_like_path(reader_memories), "consume should leave local memories as a real directory, not a shared link")
        reader_backups = sorted(reader_codex.glob("memories.bak-local-*"))
        assert_true(len(reader_backups) == 1, "consume should backup existing local memories")
        assert_true((reader_backups[0] / "MEMORY.md").read_text(encoding="utf-8") == "stale reader aggregate\n", "consume backup should keep original reader memories")
        (reader_memories / "MEMORY.md").write_text("reader-only edit\n", encoding="utf-8", newline="\n")
        assert_true((published_current / "MEMORY.md").read_text(encoding="utf-8") == "writer aggregate\n", "reader edits should not mutate published current")
        reader_doctor_stdout = io.StringIO()
        with contextlib.redirect_stdout(reader_doctor_stdout):
            reader_doctor_code = run_script(
                [
                    "--codex-dir",
                    str(reader_codex),
                    "--shared-dir",
                    str(published_shared),
                    "doctor",
                ]
            )
        reader_doctor_output = reader_doctor_stdout.getvalue()
        assert_true(reader_doctor_code == 0, "doctor should return 0 for copy-based reader diagnostics")
        assert_true("memories_local=real" in reader_doctor_output, "doctor should report consumed local memories as real")
        assert_true("memories_published=valid" in reader_doctor_output, "doctor should report valid published memories for readers")

        conflict_publish_case = case_dir / "memories-publish-conflict"
        conflict_codex = conflict_publish_case / ".codex"
        conflict_shared = conflict_publish_case / ".codex-shared"
        conflict_memories = conflict_codex / "memories"
        conflict_memories.mkdir(parents=True)
        (conflict_memories / "MEMORY.md").write_text("ok\n", encoding="utf-8", newline="\n")
        (conflict_memories / "raw_memories.sync-conflict-test.md").write_text("conflict\n", encoding="utf-8", newline="\n")
        conflict_publish_code = run_script(
            [
                "--codex-dir",
                str(conflict_codex),
                "--shared-dir",
                str(conflict_shared),
                "memories",
                "publish",
                "--apply",
            ]
        )
        assert_true(conflict_publish_code != 0, "memories publish should refuse conflict-like local memory files")
        assert_true(not (conflict_shared / "memories-published" / "current").exists(), "failed publish should not create current")

        bad_consume_case = case_dir / "memories-consume-bad-manifest"
        bad_codex = bad_consume_case / ".codex"
        bad_shared = bad_consume_case / ".codex-shared"
        bad_local = bad_codex / "memories"
        bad_current = bad_shared / "memories-published" / "current"
        bad_local.mkdir(parents=True)
        bad_current.mkdir(parents=True)
        (bad_local / "MEMORY.md").write_text("local should stay\n", encoding="utf-8", newline="\n")
        (bad_current / "MEMORY.md").write_text("published without manifest\n", encoding="utf-8", newline="\n")
        bad_consume_code = run_script(
            [
                "--codex-dir",
                str(bad_codex),
                "--shared-dir",
                str(bad_shared),
                "memories",
                "consume",
                "--apply",
            ]
        )
        assert_true(bad_consume_code != 0, "memories consume should refuse published current without manifest")
        assert_true((bad_local / "MEMORY.md").read_text(encoding="utf-8") == "local should stay\n", "failed consume should keep local memories untouched")

        adopt_case = case_dir / "memories-adopt"
        adopt_codex = adopt_case / ".codex"
        adopt_shared = adopt_case / ".codex-shared"
        adopt_local_memories = adopt_codex / "memories"
        adopt_shared_memories = adopt_shared / "memories"
        adopt_local_memories.mkdir(parents=True)
        (adopt_local_memories / ".git").mkdir()
        (adopt_local_memories / ".agents").mkdir()
        (adopt_local_memories / ".codex").write_text("codex internal marker\n", encoding="utf-8", newline="\n")
        (adopt_local_memories / "MEMORY.md").write_text("local writer memory\n", encoding="utf-8", newline="\n")

        adopt_dry_run_code = run_script(
            [
                "--codex-dir",
                str(adopt_codex),
                "--shared-dir",
                str(adopt_shared),
                "memories",
                "adopt",
            ]
        )
        assert_true(adopt_dry_run_code == 0, "dry-run memories adopt should return 0")
        assert_true(not adopt_shared_memories.exists(), "dry-run memories adopt should not create shared memories")
        assert_true((adopt_local_memories / "MEMORY.md").is_file(), "dry-run memories adopt should keep local memories")

        adopt_apply_code = run_script(
            [
                "--codex-dir",
                str(adopt_codex),
                "--shared-dir",
                str(adopt_shared),
                "memories",
                "adopt",
                "--apply",
            ]
        )
        assert_true(adopt_apply_code == 0, "apply memories adopt should return 0")
        assert_true((adopt_shared_memories / "MEMORY.md").read_text(encoding="utf-8") == "local writer memory\n", "adopt should copy local memories into shared")
        assert_true(same_resolved_path(adopt_local_memories, adopt_shared_memories), "adopt should expose shared memories through local path")
        assert_true((adopt_local_memories / ".git").is_dir(), "adopt should keep .git active through the local memory link")
        assert_true((adopt_local_memories / ".agents").is_dir(), "adopt should keep .agents active through the local memory link")
        assert_true((adopt_local_memories / ".codex").is_file(), "adopt should keep .codex active through the local memory link")
        adopt_backups = sorted(adopt_codex.glob("memories.bak-local-*"))
        assert_true(len(adopt_backups) == 1, "adopt should backup original local memories")
        assert_true((adopt_backups[0] / "MEMORY.md").read_text(encoding="utf-8") == "local writer memory\n", "adopt backup should keep original local memories")

        link_case = case_dir / "memories-link"
        link_codex = link_case / ".codex"
        link_shared = link_case / ".codex-shared"
        link_local_memories = link_codex / "memories"
        link_shared_memories = link_shared / "memories"
        link_local_memories.mkdir(parents=True)
        link_shared_memories.mkdir(parents=True)
        (link_shared_memories / ".git").mkdir()
        (link_shared_memories / ".agents").mkdir()
        (link_shared_memories / ".codex").write_text("codex internal marker\n", encoding="utf-8", newline="\n")
        (link_local_memories / "MEMORY.md").write_text("reader-local memory\n", encoding="utf-8", newline="\n")
        (link_shared_memories / "MEMORY.md").write_text("shared memory\n", encoding="utf-8", newline="\n")

        link_apply_code = run_script(
            [
                "--codex-dir",
                str(link_codex),
                "--shared-dir",
                str(link_shared),
                "memories",
                "link",
                "--apply",
            ]
        )
        assert_true(link_apply_code == 0, "apply memories link should return 0")
        assert_true(same_resolved_path(link_local_memories, link_shared_memories), "link should expose shared memories through local path")
        assert_true((link_local_memories / ".git").is_dir(), "link should keep .git active through the local memory link")
        assert_true((link_local_memories / ".agents").is_dir(), "link should keep .agents active through the local memory link")
        assert_true((link_local_memories / ".codex").is_file(), "link should keep .codex active through the local memory link")
        assert_true((link_local_memories / "MEMORY.md").read_text(encoding="utf-8") == "shared memory\n", "link should not overwrite shared memories")
        link_backups = sorted(link_codex.glob("memories.bak-local-*"))
        assert_true(len(link_backups) == 1, "link should backup existing local memories")
        assert_true((link_backups[0] / "MEMORY.md").read_text(encoding="utf-8") == "reader-local memory\n", "link backup should keep original reader memories")

        stub_case = case_dir / "link-stub"
        stub_link = stub_case / "local-link"
        stub_target = stub_case / "shared-target"
        stub_ctx = Context(stub_case / ".codex", stub_case / ".codex-shared", apply=True)
        stub_target.mkdir(parents=True)
        (stub_target / "value.txt").write_text("shared\n", encoding="utf-8", newline="\n")
        stub_link.mkdir(parents=True)
        assert_true(create_directory_link(stub_ctx, stub_link, stub_target), "create_directory_link should recover from an empty existing link stub")
        assert_true(same_resolved_path(stub_link, stub_target), "recovered link should resolve to target")
        stub_backups = sorted(stub_case.glob("local-link.link-stub-bak-*"))
        assert_true(len(stub_backups) == 1, "empty link stub should be preserved as backup")

        junction_stub_case = case_dir / "junction-stub"
        junction_stub_link = junction_stub_case / "local-link"
        junction_stub_target = junction_stub_case / "shared-target"
        junction_stub_ctx = Context(junction_stub_case / ".codex", junction_stub_case / ".codex-shared", apply=True)
        junction_stub_target.mkdir(parents=True)
        (junction_stub_target / "value.txt").write_text("shared\n", encoding="utf-8", newline="\n")
        junction_stub_link.mkdir(parents=True)
        if is_windows():
            assert_true(create_windows_junction(junction_stub_ctx, junction_stub_link, junction_stub_target), "create_windows_junction should recover from an empty existing link stub")
            assert_true(same_resolved_path(junction_stub_link, junction_stub_target), "recovered junction should resolve to target")
            junction_stub_backups = sorted(junction_stub_case.glob("local-link.link-stub-bak-*"))
            assert_true(len(junction_stub_backups) == 1, "junction empty link stub should be preserved as backup")

        print("self-test: PASS")
        return 0
    finally:
        safe_cleanup_self_test_case(case_dir, base)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Context(args.codex_dir.expanduser(), args.shared_dir.expanduser(), args.apply, args.verbose)

    if args.command == "install":
        return command_install(ctx, configure_syncthing=args.configure_syncthing, args=args)
    if args.command == "doctor":
        return command_doctor(ctx, args)
    if args.command == "snapshot":
        return command_snapshot(ctx)
    if args.command == "install-cli":
        return command_install_cli(ctx, args)
    if args.command == "version":
        return command_version()
    if args.command == "memories":
        if args.memories_command == "adopt":
            return command_memories_adopt(ctx)
        if args.memories_command == "link":
            return command_memories_link(ctx)
        if args.memories_command == "publish":
            return command_memories_publish(ctx)
        if args.memories_command == "consume":
            return command_memories_consume(ctx)
    if args.command == "self-test":
        return command_self_test()

    ctx.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
