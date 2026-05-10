#!/usr/bin/env python3
"""Onboard a machine into a shared Codex layer.

Dry-run is the default. Real filesystem changes require --apply.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import ntpath
import os
import platform
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
DEFAULT_SYNCTHING_URL = "http://127.0.0.1:8384"
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


def verify_memory_link(ctx: Context, link: Path, target: Path) -> bool:
    if not ctx.apply:
        return True
    if same_resolved_path(link, target):
        return True
    ctx.error(f"Memory link verification failed: {link} does not resolve to {target}")
    return False


def warn_memory_key_files(ctx: Context, root: Path, label: str) -> None:
    for name in MEMORY_KEY_FILES:
        if (root / name).is_file():
            continue
        ctx.warn(f"{label} is missing common Codex memory file: {root / name}")


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
    if not create_directory_link(ctx, local, shared):
        if ctx.apply and not path_exists_or_link(local) and backup.exists():
            try:
                backup.rename(local)
            except OSError as exc:
                ctx.error(f"Failed to restore local memories backup {backup} -> {local}: {exc}")
        return 1
    verify_memory_link(ctx, local, shared)
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
        if not local.is_dir() or is_link_like_path(local):
            ctx.error(f"Local memories path exists but is not a replaceable real directory: {local}")
            return 1
        if not validate_no_conflicts(ctx, local, "local memories"):
            return 1
        backup = next_backup_path(local)
        if not rename_path(ctx, local, backup, "local memories to backup"):
            return 1
    if not create_directory_link(ctx, local, shared):
        return 1
    verify_memory_link(ctx, local, shared)
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
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Prepare Codex shared skills and diagnostics.")
    parser.add_argument("--codex-dir", type=Path, default=default_codex_dir())
    parser.add_argument("--shared-dir", type=Path, default=default_shared_dir())
    parser.add_argument("--apply", action="store_true", help="Actually change files. Default is dry-run.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--syncthing-url", default=DEFAULT_SYNCTHING_URL)
    parser.add_argument("--syncthing-api-key", default=None)

    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install", help="Prepare shared folder and link shared user skills.")
    install.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    install.add_argument("--configure-syncthing", action="store_true")
    sub.add_parser("doctor", help="Diagnose shared Codex setup.")
    snapshot = sub.add_parser("snapshot", help="Create a local Git snapshot of .codex-shared.")
    snapshot.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    memories = sub.add_parser("memories", help="Manage Codex memories links between .codex and .codex-shared.")
    memories_sub = memories.add_subparsers(dest="memories_command", required=True)
    adopt = memories_sub.add_parser("adopt", help="Make the current local memories directory the shared source.")
    adopt.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    link = memories_sub.add_parser("link", help="Link local memories to an existing shared memories directory.")
    link.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    install_cli = sub.add_parser("install-cli", help=f"Install a local '{APP_NAME}' launcher.")
    install_cli.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="Actually change files. Default is dry-run.")
    install_cli.add_argument("--bin-dir", type=Path, default=default_bin_dir(), help="Directory where the launcher should be installed.")
    install_cli.add_argument("--force", action="store_true", help="Overwrite an existing launcher with different content.")
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


def render_cli_launcher(script_path: Path) -> str:
    if is_windows():
        return f"@echo off\r\npy -3 \"{script_path}\" %*\r\n"
    return f"#!/usr/bin/env sh\nexec python3 {shlex.quote(str(script_path))} \"$@\"\n"


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
    if is_windows():
        ensure_windows_user_path(ctx, bin_dir)
    else:
        ctx.info(f"Make sure {bin_dir} is on PATH before running {APP_NAME}.")
    return 1 if ctx.errors else 0


def find_conflict_files(root: Path) -> list[Path]:
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
            name = filename.casefold()
            if "sync-conflict" not in name and "conflict" not in name:
                continue
            path = current_path / filename
            try:
                path.stat()
            except OSError:
                continue
            conflicts.append(path)
    return sorted(conflicts, key=lambda item: str(item).casefold())


def tool_path(name: str) -> str | None:
    return shutil.which(name)


def command_doctor(ctx: Context) -> int:
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

    for path in find_conflict_files(ctx.shared_dir):
        ctx.warn(f"Conflict-like file found: {path}")

    print(f"python={sys.version.split()[0]} ({sys.executable})")
    git = tool_path("git")
    syncthing = tool_path("syncthing")
    print(f"git={git if git else 'missing'}")
    print(f"syncthing={syncthing if syncthing else 'missing'}")

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
        cli_launcher = cli_bin_dir / APP_NAME
        cli_dry_run_code = run_script(
            [
                "install-cli",
                "--bin-dir",
                str(cli_bin_dir),
            ]
        )
        assert_true(cli_dry_run_code == 0, "dry-run install-cli should return 0")
        assert_true(not cli_launcher.exists(), "dry-run install-cli should not create launcher")

        cli_apply_code = run_script(
            [
                "install-cli",
                "--bin-dir",
                str(cli_bin_dir),
                "--apply",
            ]
        )
        assert_true(cli_apply_code == 0, "apply install-cli should return 0")
        assert_true(cli_launcher.is_file(), "install-cli should create launcher")
        assert_true(windows_path_contains("C:\\Tools;C:\\Users\\Admin\\.local\\bin", Path("C:/Users/Admin/.local/bin")), "windows_path_contains should match normalized paths")
        assert_true(append_windows_path("C:\\Tools;", Path("C:/Users/Admin/.local/bin")) == "C:\\Tools;C:/Users/Admin/.local/bin", "append_windows_path should append without duplicate separator")
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

        adopt_case = case_dir / "memories-adopt"
        adopt_codex = adopt_case / ".codex"
        adopt_shared = adopt_case / ".codex-shared"
        adopt_local_memories = adopt_codex / "memories"
        adopt_shared_memories = adopt_shared / "memories"
        adopt_local_memories.mkdir(parents=True)
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
        return command_doctor(ctx)
    if args.command == "snapshot":
        return command_snapshot(ctx)
    if args.command == "install-cli":
        return command_install_cli(ctx, args)
    if args.command == "memories":
        if args.memories_command == "adopt":
            return command_memories_adopt(ctx)
        if args.memories_command == "link":
            return command_memories_link(ctx)
    if args.command == "self-test":
        return command_self_test()

    ctx.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
