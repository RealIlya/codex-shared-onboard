# Codex Shared Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one safe cross-platform Python onboarding script for linking local Codex user skills to a Syncthing-backed `.codex-shared` layer.

**Architecture:** The script keeps active `.codex` runtime directories local and links only user skills from `.codex-shared/skills-user`. It uses dry-run by default, writes policy/support files into `.codex-shared`, provides diagnostics for Syncthing and memories, and includes a self-test command that operates only on temporary directories.

**Tech Stack:** Python 3 standard library only, Windows junction fallback through `cmd /c mklink /J`, optional Syncthing REST API through `urllib.request`, Git through subprocess for snapshots.

---

## File Structure

- Create: `codex_shared_onboard.py`
  - One-file command-line utility.
  - Contains CLI parsing, path detection, action planning, filesystem operations, Syncthing diagnostics, Git snapshot command, and self-tests.
- Existing: `docs/superpowers/specs/2026-04-29-codex-shared-onboarding-design.md`
  - Design source of truth.
- Create: `docs/superpowers/plans/2026-04-29-codex-shared-onboarding.md`
  - This implementation plan.

## Task 1: Add Script Skeleton and CLI

**Files:**
- Create: `codex_shared_onboard.py`

- [ ] **Step 1: Create the initial script with CLI commands**

Use `apply_patch` to create `codex_shared_onboard.py` with this content:

```python
#!/usr/bin/env python3
"""Onboard a machine into a shared Codex layer.

Dry-run is the default. Real filesystem changes require --apply.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


APP_NAME = "codex-shared-onboard"
DEFAULT_SYNCTHING_URL = "http://127.0.0.1:8384"


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
    candidates = []
    for child in users_dir.iterdir():
        if child.name.lower() in {"public", "default", "default user", "all users"}:
            continue
        if (child / ".codex-shared").exists() or child.name.lower() == "admin":
            candidates.append(child.name)
    return candidates[0] if candidates else None


def default_shared_dir() -> Path:
    if is_wsl():
        win_user = detect_wsl_windows_user()
        if win_user:
            return Path("/mnt/c/Users") / win_user / ".codex-shared"
    return Path.home() / ".codex-shared"


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
    def shared_skills_dir(self) -> Path:
        return self.shared_dir / "skills-user"

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Codex shared skills and diagnostics.")
    parser.add_argument("--codex-dir", type=Path, default=default_codex_dir())
    parser.add_argument("--shared-dir", type=Path, default=default_shared_dir())
    parser.add_argument("--apply", action="store_true", help="Actually change files. Default is dry-run.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--syncthing-url", default=DEFAULT_SYNCTHING_URL)
    parser.add_argument("--syncthing-api-key", default=None)

    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install", help="Prepare shared folder and link shared user skills.")
    install.add_argument("--configure-syncthing", action="store_true")
    sub.add_parser("doctor", help="Diagnose shared Codex setup.")
    sub.add_parser("snapshot", help="Create a local Git snapshot of .codex-shared.")
    sub.add_parser("self-test", help="Run tests in temporary folders only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Context(args.codex_dir.expanduser(), args.shared_dir.expanduser(), args.apply, args.verbose)

    if args.command == "install":
        return command_install(ctx, configure_syncthing=args.configure_syncthing, args=args)
    if args.command == "doctor":
        return command_doctor(ctx)
    if args.command == "snapshot":
        return command_snapshot(ctx)
    if args.command == "self-test":
        return command_self_test()

    ctx.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run syntax check and record the expected failure**

Run:

```powershell
python -m py_compile .\codex_shared_onboard.py
```

Expected: fail with unresolved names such as `command_install`, because later tasks add those functions.

- [ ] **Step 3: Commit or checkpoint**

If this folder is a Git repo:

```bash
git add codex_shared_onboard.py
git commit -m "chore: scaffold Codex shared onboarding CLI"
```

If it is not a Git repo, skip the commit and note that no commit was made.

## Task 2: Implement Policy Files and Safe Directory Creation

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add constants for `.stignore` and memory policy**

Insert below `DEFAULT_SYNCTHING_URL`:

```python
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
```

- [ ] **Step 2: Add safe write helpers**

Insert before `build_parser()`:

```python
def ensure_dir(ctx: Context, path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            ctx.error(f"Expected directory but found file: {path}")
        return
    ctx.plan(f"create directory {path}")
    if ctx.apply:
        path.mkdir(parents=True, exist_ok=True)


def write_text_if_missing(ctx: Context, path: Path, text: str) -> None:
    if path.exists():
        return
    ctx.plan(f"write {path}")
    if ctx.apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
```

- [ ] **Step 3: Add `prepare_shared_layout()`**

Insert before `build_parser()`:

```python
def prepare_shared_layout(ctx: Context) -> None:
    ensure_dir(ctx, ctx.shared_dir)
    ensure_dir(ctx, ctx.shared_skills_dir)
    ensure_dir(ctx, ctx.shared_dir / "tools")
    write_text_if_missing(ctx, ctx.shared_dir / ".stignore", STIGNORE_TEXT)
    write_text_if_missing(ctx, ctx.shared_dir / "memory-policy.md", MEMORY_POLICY_TEXT)
```

- [ ] **Step 4: Add temporary placeholder command implementations**

Insert before `main()`:

```python
def command_install(ctx: Context, configure_syncthing: bool, args: argparse.Namespace) -> int:
    prepare_shared_layout(ctx)
    if configure_syncthing:
        ctx.warn("Syncthing configuration will be implemented in a later task.")
    return 1 if ctx.errors else 0


def command_doctor(ctx: Context) -> int:
    print(f"codex_dir={ctx.codex_dir}")
    print(f"shared_dir={ctx.shared_dir}")
    return 0


def command_snapshot(ctx: Context) -> int:
    ctx.warn("Snapshot will be implemented in a later task.")
    return 0


def command_self_test() -> int:
    print("self-test will be implemented in a later task")
    return 0
```

- [ ] **Step 5: Run dry-run install on sandbox paths**

Run:

```powershell
python .\codex_shared_onboard.py --codex-dir .\.onboard-test-tmp\.codex --shared-dir .\.onboard-test-tmp\.codex-shared install
```

Expected: prints planned directory and file writes. The `.onboard-test-tmp` directory must not exist afterward.

- [ ] **Step 6: Run apply install on sandbox paths**

Run:

```powershell
python .\codex_shared_onboard.py --codex-dir .\.onboard-test-tmp\.codex --shared-dir .\.onboard-test-tmp\.codex-shared --apply install
```

Expected: creates `.onboard-test-tmp\.codex-shared\skills-user`, `.stignore`, `memory-policy.md`, and `tools`.

## Task 3: Implement Skill Discovery and Link Planning

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add skill discovery helpers**

Insert before `prepare_shared_layout()`:

```python
def iter_shared_skills(ctx: Context) -> list[Path]:
    if not ctx.shared_skills_dir.exists():
        return []
    skills = []
    for child in sorted(ctx.shared_skills_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name == ".system":
            continue
        if (child / "SKILL.md").is_file():
            skills.append(child)
        else:
            ctx.warn(f"Skipping shared skill without SKILL.md: {child}")
    return skills


def same_resolved_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=True) == right.resolve(strict=True)
    except OSError:
        return False
```

- [ ] **Step 2: Add backup-name helper**

Insert after `same_resolved_path()`:

```python
def next_backup_path(path: Path) -> Path:
    base = path.with_name(f"{path.name}.bak-local-{now_stamp()}")
    if not base.exists():
        return base
    for index in range(2, 100):
        candidate = path.with_name(f"{path.name}.bak-local-{now_stamp()}-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate backup name for {path}")
```

- [ ] **Step 3: Add link operation helpers**

Insert after `next_backup_path()`:

```python
def create_windows_junction(ctx: Context, link: Path, target: Path) -> None:
    command = ["cmd", "/c", "mklink", "/J", str(link), str(target)]
    ctx.plan(f"create Windows junction {link} -> {target}")
    if ctx.apply:
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode != 0:
            ctx.error(f"Failed to create junction {link}: {result.stderr.strip() or result.stdout.strip()}")


def create_directory_link(ctx: Context, link: Path, target: Path) -> None:
    ctx.plan(f"create directory link {link} -> {target}")
    if not ctx.apply:
        return
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError as exc:
        if is_windows():
            ctx.warn(f"Symlink failed, trying junction instead: {exc}")
            create_windows_junction(ctx, link, target)
            return
        ctx.error(f"Failed to create symlink {link} -> {target}: {exc}")
```

- [ ] **Step 4: Add `link_shared_skills()`**

Insert after `create_directory_link()`:

```python
def link_shared_skills(ctx: Context) -> None:
    ensure_dir(ctx, ctx.codex_dir)
    ensure_dir(ctx, ctx.skills_dir)
    skills = iter_shared_skills(ctx)
    if not skills:
        ctx.warn(f"No shared user skills found in {ctx.shared_skills_dir}")
        return

    for shared_skill in skills:
        local_skill = ctx.skills_dir / shared_skill.name
        if local_skill.name == ".system":
            continue
        if local_skill.exists() or local_skill.is_symlink():
            if same_resolved_path(local_skill, shared_skill):
                ctx.info(f"already linked: {local_skill} -> {shared_skill}")
                continue
            if local_skill.is_symlink():
                ctx.plan(f"replace wrong symlink {local_skill}")
                if ctx.apply:
                    local_skill.unlink()
            else:
                backup = next_backup_path(local_skill)
                ctx.plan(f"backup existing local skill {local_skill} -> {backup}")
                if ctx.apply:
                    local_skill.rename(backup)
        create_directory_link(ctx, local_skill, shared_skill)
```

- [ ] **Step 5: Update `command_install()`**

Replace the body with:

```python
def command_install(ctx: Context, configure_syncthing: bool, args: argparse.Namespace) -> int:
    prepare_shared_layout(ctx)
    link_shared_skills(ctx)
    if configure_syncthing:
        configure_syncthing_folder(ctx, args.syncthing_url, args.syncthing_api_key)
    return 1 if ctx.errors else 0
```

This will reference `configure_syncthing_folder()` before it exists; the next task defines it.

- [ ] **Step 6: Add a temporary Syncthing stub to keep syntax runnable**

Insert before `command_install()`:

```python
def configure_syncthing_folder(ctx: Context, url: str, api_key: str | None) -> None:
    ctx.warn("Syncthing configuration is not implemented yet.")
```

- [ ] **Step 7: Run syntax check**

Run:

```powershell
python -m py_compile .\codex_shared_onboard.py
```

Expected: no output and exit code 0.

- [ ] **Step 8: Test skill linking in sandbox**

Create sandbox fixture manually through PowerShell:

```powershell
New-Item -ItemType Directory -Force .\.onboard-test-tmp\.codex\skills\demo-skill | Out-Null
New-Item -ItemType Directory -Force .\.onboard-test-tmp\.codex-shared\skills-user\demo-skill | Out-Null
Set-Content .\.onboard-test-tmp\.codex-shared\skills-user\demo-skill\SKILL.md "---`nname: demo-skill`ndescription: demo`n---`n"
python .\codex_shared_onboard.py --codex-dir .\.onboard-test-tmp\.codex --shared-dir .\.onboard-test-tmp\.codex-shared --apply install
```

Expected: existing local `demo-skill` is renamed to `demo-skill.bak-local-*`, and `.codex\skills\demo-skill` becomes a link or junction to shared `demo-skill`.

## Task 4: Implement Doctor Diagnostics

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add conflict and tool detection helpers**

Insert before `command_doctor()`:

```python
def find_conflict_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    matches = []
    for path in root.rglob("*"):
        name = path.name.lower()
        if "sync-conflict" in name or "conflict" in name:
            matches.append(path)
    return matches


def tool_path(name: str) -> str | None:
    return shutil.which(name)
```

- [ ] **Step 2: Replace `command_doctor()`**

Replace the function with:

```python
def command_doctor(ctx: Context) -> int:
    print(f"platform={platform.platform()}")
    print(f"is_windows={is_windows()}")
    print(f"is_wsl={is_wsl()}")
    print(f"codex_dir={ctx.codex_dir}")
    print(f"shared_dir={ctx.shared_dir}")

    if not ctx.codex_dir.exists():
        ctx.warn(f"Codex directory does not exist: {ctx.codex_dir}")
    if not ctx.shared_dir.exists():
        ctx.warn(f"Shared directory does not exist: {ctx.shared_dir}")
    if not (ctx.shared_dir / "memory-policy.md").exists():
        ctx.warn("memory-policy.md is missing")
    if not (ctx.shared_dir / ".stignore").exists():
        ctx.warn(".stignore is missing")

    for conflict in find_conflict_files(ctx.shared_dir):
        ctx.warn(f"Syncthing conflict-like file detected: {conflict}")

    print(f"python={sys.version.split()[0]}")
    print(f"git={tool_path('git') or 'missing'}")
    print(f"syncthing={tool_path('syncthing') or 'missing'}")

    for shared_skill in iter_shared_skills(ctx):
        local_skill = ctx.skills_dir / shared_skill.name
        if same_resolved_path(local_skill, shared_skill):
            print(f"skill linked: {shared_skill.name}")
        elif local_skill.exists() or local_skill.is_symlink():
            ctx.warn(f"skill exists locally but does not point to shared: {local_skill}")
        else:
            ctx.warn(f"shared skill is not linked locally: {shared_skill.name}")

    return 1 if ctx.errors else 0
```

- [ ] **Step 3: Run doctor on sandbox**

Run:

```powershell
python .\codex_shared_onboard.py --codex-dir .\.onboard-test-tmp\.codex --shared-dir .\.onboard-test-tmp\.codex-shared doctor
```

Expected: prints platform, paths, Python version, Git/Syncthing availability, and linked skill status.

## Task 5: Implement Syncthing Diagnostics and Folder Configuration

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add Syncthing config path helpers**

Insert before `configure_syncthing_folder()`:

```python
def syncthing_config_candidates() -> list[Path]:
    candidates = []
    if is_windows():
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Syncthing" / "config.xml")
    else:
        candidates.append(Path.home() / ".config" / "syncthing" / "config.xml")
        candidates.append(Path.home() / ".local" / "state" / "syncthing" / "config.xml")
    return candidates


def read_syncthing_api_key() -> str | None:
    import xml.etree.ElementTree as ET

    for config in syncthing_config_candidates():
        if not config.exists():
            continue
        try:
            root = ET.parse(config).getroot()
        except ET.ParseError:
            continue
        gui = root.find("gui")
        if gui is None:
            continue
        api_key = gui.findtext("apikey")
        if api_key:
            return api_key.strip()
    return None
```

- [ ] **Step 2: Add Syncthing HTTP helpers**

Insert after `read_syncthing_api_key()`:

```python
def syncthing_request(url: str, api_key: str, path: str, method: str = "GET", body: object | None = None) -> tuple[int, str]:
    data = None
    headers = {"X-API-Key": api_key}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url.rstrip("/") + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read().decode("utf-8", errors="replace")
```

- [ ] **Step 3: Replace `configure_syncthing_folder()`**

Replace the stub with:

```python
def configure_syncthing_folder(ctx: Context, url: str, api_key: str | None) -> None:
    if tool_path("syncthing") is None:
        ctx.warn("Syncthing binary is missing from PATH. Install Syncthing or configure the folder manually.")

    key = api_key or read_syncthing_api_key(ctx)
    if not key:
        ctx.warn("Syncthing API key not found. Pass --syncthing-api-key or configure the folder manually in Syncthing UI.")
        return

    folder_id = "codex-shared"
    folder_path = str(ctx.shared_dir.resolve() if ctx.shared_dir.exists() else ctx.shared_dir)

    try:
        _, folders_text = syncthing_request(url, key, "/rest/config/folders")
    except urllib.error.URLError as exc:
        ctx.warn(f"Syncthing API is not reachable at {url}: {exc}")
        return

    try:
        folders = json.loads(folders_text)
    except json.JSONDecodeError as exc:
        ctx.warn(f"Syncthing API returned invalid JSON: {exc}")
        return

    for folder in folders:
        if folder.get("id") == folder_id:
            existing = folder.get("path")
            if existing == folder_path:
                ctx.info(f"Syncthing folder already configured: {folder_id} -> {folder_path}")
            else:
                ctx.warn(f"Syncthing folder id {folder_id} already exists with different path: {existing}")
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
    if ctx.apply:
        syncthing_request(url, key, "/rest/config/folders", method="POST", body=new_folder)
        ctx.info("Syncthing config updated. Remote devices still need to be approved in Syncthing UI.")
```

- [ ] **Step 4: Run dry-run Syncthing configuration**

Run:

```powershell
python .\codex_shared_onboard.py --codex-dir .\.onboard-test-tmp\.codex --shared-dir .\.onboard-test-tmp\.codex-shared install --configure-syncthing
```

Expected: if Syncthing/API is unavailable, script prints a warning and exits without crashing.

## Task 6: Implement Git Snapshot

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add subprocess helper**

Insert before `command_snapshot()`:

```python
def run_checked(ctx: Context, command: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    if ctx.verbose:
        print(f"run: {' '.join(command)} cwd={cwd}")
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        ctx.error(f"Failed to run {' '.join(command)}: {exc}")
        return None
```

- [ ] **Step 2: Replace `command_snapshot()`**

Replace the function with:

```python
def command_snapshot(ctx: Context) -> int:
    if tool_path("git") is None:
        ctx.warn("Git is missing from PATH. Snapshot skipped.")
        return 0
    ensure_dir(ctx, ctx.shared_dir)
    git_dir = ctx.shared_dir / ".git"
    if not git_dir.exists():
        ctx.plan(f"initialize Git repository in {ctx.shared_dir}")
        if ctx.apply:
            result = run_checked(ctx, ["git", "init"], ctx.shared_dir)
            if result is None or result.returncode != 0:
                ctx.error("git init failed")
                return 1
    ctx.plan("stage shared Codex files")
    if ctx.apply:
        run_checked(ctx, ["git", "add", "."], ctx.shared_dir)
        status = run_checked(ctx, ["git", "status", "--porcelain"], ctx.shared_dir)
        if status is None:
            return 1
        if not status.stdout.strip():
            ctx.info("No changes to snapshot.")
            return 0
        message = f"snapshot: codex shared layer {dt.datetime.now().isoformat(timespec='seconds')}"
        result = run_checked(ctx, ["git", "commit", "-m", message], ctx.shared_dir)
        if result is None or result.returncode != 0:
            ctx.error((result.stderr if result else "git commit failed").strip())
            return 1
        ctx.info(message)
    return 1 if ctx.errors else 0
```

- [ ] **Step 3: Run dry-run snapshot**

Run:

```powershell
python .\codex_shared_onboard.py --shared-dir .\.onboard-test-tmp\.codex-shared snapshot
```

Expected: prints planned Git initialization/staging without creating `.git`.

## Task 7: Implement Built-In Self Tests

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add test helpers**

Insert before `command_self_test()`:

```python
def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_script(args: list[str]) -> int:
    return main(args)
```

- [ ] **Step 2: Replace `command_self_test()`**

Replace the function with:

```python
def command_self_test() -> int:
    root = Path.cwd() / ".onboard-test-tmp"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    try:
        codex = root / ".codex"
        shared = root / ".codex-shared"

        exit_code = run_script(["--codex-dir", str(codex), "--shared-dir", str(shared), "install"])
        assert_true(exit_code == 0, "dry-run install should succeed")
        assert_true(not shared.exists(), "dry-run must not create shared dir")

        skill = shared / "skills-user" / "demo-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: demo-skill\ndescription: demo\n---\n", encoding="utf-8")
        local_skill = codex / "skills" / "demo-skill"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text("---\nname: old-demo\ndescription: old\n---\n", encoding="utf-8")

        exit_code = run_script(["--codex-dir", str(codex), "--shared-dir", str(shared), "--apply", "install"])
        assert_true(exit_code == 0, "apply install should succeed")
        backups = list((codex / "skills").glob("demo-skill.bak-local-*"))
        assert_true(bool(backups), "existing local skill should be backed up")
        assert_true((codex / "skills" / "demo-skill" / "SKILL.md").exists(), "linked skill should expose SKILL.md")
        assert_true((shared / ".stignore").exists(), ".stignore should be created")
        assert_true((shared / "memory-policy.md").exists(), "memory policy should be created")

        conflict = shared / "memories" / "MEMORY.sync-conflict-test.md"
        conflict.parent.mkdir()
        conflict.write_text("conflict", encoding="utf-8")
        exit_code = run_script(["--codex-dir", str(codex), "--shared-dir", str(shared), "doctor"])
        assert_true(exit_code == 0, "doctor should not fail on warnings")

        print("self-test: PASS")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 3: Run self-test**

Run:

```powershell
python .\codex_shared_onboard.py self-test
```

Expected: `self-test: PASS`.

## Task 8: Final Verification Against Real Paths in Dry-Run

**Files:**
- No file changes expected.

- [ ] **Step 1: Run syntax check**

Run:

```powershell
python -m py_compile .\codex_shared_onboard.py
```

Expected: no output and exit code 0.

- [ ] **Step 2: Run self-test**

Run:

```powershell
python .\codex_shared_onboard.py self-test
```

Expected: `self-test: PASS`.

- [ ] **Step 3: Run doctor against real defaults**

Run:

```powershell
python .\codex_shared_onboard.py doctor
```

Expected: prints real `codex_dir` and `shared_dir`, detects existing `gl-commit-message-helper`, and reports warnings without modifying files.

- [ ] **Step 4: Run install dry-run against real defaults**

Run:

```powershell
python .\codex_shared_onboard.py install
```

Expected: prints planned operations only. No real files are changed because `--apply` is absent.

- [ ] **Step 5: Stop before real apply**

Do not run:

```powershell
python .\codex_shared_onboard.py install --apply
```

until the user reviews the dry-run output.

## Self-Review

Spec coverage:
- The plan covers one-file Python implementation, dry-run default, safe skill linking, `.system` exclusion, Syncthing diagnostics/config, memory policy, snapshot, and self-test.

Placeholder scan:
- No unfinished-marker text or unspecified test steps remain.

Type consistency:
- `Context`, `Path`, `command_install`, `command_doctor`, `command_snapshot`, `command_self_test`, and helper names are consistent across tasks.

Risk notes:
- Real user `.codex` is not touched by `self-test`.
- Real install without `--apply` is dry-run.
- Real `--apply` is explicitly deferred for user review.
