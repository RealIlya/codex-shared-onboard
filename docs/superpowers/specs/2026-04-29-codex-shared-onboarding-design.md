# Codex Shared Onboarding Design

## Goal

Build one cross-platform onboarding script for joining a machine to the shared Codex layer used with Syncthing.

The script must be safe by default. It must never delete or rewrite an active `.codex` runtime directory. It prepares `.codex-shared`, links user skills into the local `.codex/skills`, diagnoses memory-sync risks, and optionally helps configure a local Syncthing folder.

## Architecture

Each machine keeps its own local Codex runtime:

```text
~/.codex
```

Shared state lives separately:

```text
~/.codex-shared
```

Syncthing syncs only `.codex-shared`, not `.codex`.

The first supported shared content is:

```text
.codex-shared/
  skills-user/
  tools/
  .stignore
  memory-policy.md
```

`memories/` is supported by diagnostics and policy, but is not linked automatically in the first install flow. Official Codex memories are generated state, so they require a one-writer policy.

## Script Interface

The script is one Python file:

```text
codex_shared_onboard.py
```

Commands:

```bash
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py install --configure-syncthing --apply
python codex_shared_onboard.py snapshot --apply
python codex_shared_onboard.py self-test
```

Global options:

```bash
--codex-dir PATH
--shared-dir PATH
--syncthing-api-key KEY
--syncthing-url URL
--apply
--verbose
```

Default behavior is dry-run unless `--apply` is present.

## Path Rules

Default paths:

```text
Windows:
  codex-dir  = %USERPROFILE%\.codex
  shared-dir = %USERPROFILE%\.codex-shared

Linux:
  codex-dir  = $HOME/.codex
  shared-dir = $HOME/.codex-shared

WSL:
  codex-dir  = $HOME/.codex
  shared-dir = /mnt/c/Users/<WindowsUser>/.codex-shared if detectable, otherwise $HOME/.codex-shared
```

The user can override both paths.

## Safety Contract

The script must never:

- delete `.codex`
- delete `.codex/skills`
- delete `.codex/skills/.system`
- edit `.codex/config.toml`
- edit `.codex/auth.json`
- edit `.codex/rules`
- edit `.codex/sessions`
- edit `.codex/history.jsonl`
- edit `.codex/state_*.sqlite*`
- edit `.codex/logs_*.sqlite*`
- delete local user skills

If `.codex/skills/<skill>` exists as a normal directory, the script may rename it to:

```text
<skill>.bak-local-YYYYMMDD-HHMMSS
```

Only after backup rename may it create a link to:

```text
.codex-shared/skills-user/<skill>
```

If the existing path is already a link or junction to the expected target, no action is needed.

## Linking Behavior

The script scans:

```text
.codex-shared/skills-user/*
```

Only directories containing `SKILL.md` are considered valid skills.

Windows behavior:

- Try `os.symlink(..., target_is_directory=True)`.
- If symlink creation fails due to privilege restrictions, create a directory junction with `mklink /J`.
- If junction creation fails, report the exact reason and leave the local skill untouched.

Linux and WSL behavior:

- Use `os.symlink`.

`.system` is always local and never linked from `.codex-shared`.

## Syncthing Behavior

Without `--configure-syncthing`, the script only prints diagnostics.

With `--configure-syncthing`, the script:

- checks whether `syncthing` is on `PATH`
- checks local API availability at `http://127.0.0.1:8384`
- reads the API key from Syncthing config when possible
- accepts `--syncthing-api-key`
- adds or verifies a folder for `.codex-shared`
- does not auto-trust remote devices

Missing programs and services are reported explicitly.

## Memory Policy

The script writes `.codex-shared/memory-policy.md` with these rules:

- official Codex memories are generated state
- use one writer and many readers
- conflict files must be reported before memory-derived assumptions are used
- do not manually edit generated memory aggregates unless explicitly requested

`doctor` checks for:

- `*sync-conflict*`
- duplicate conflict-looking files
- missing `memory-policy.md`
- `memories/` linked when no policy exists

## Snapshot Behavior

The `snapshot` command uses Git inside `.codex-shared` if available.

It must:

- initialize a repo if needed and `--apply` is present
- show clear instructions if Git is missing
- commit current `.codex-shared` state with a timestamped message
- never push

## Testing Strategy

The `self-test` command creates temporary directories under the current working directory, for example:

```text
.onboard-test-tmp/
  case-basic/
    .codex/
    .codex-shared/
```

Tests cover:

- dry-run produces no filesystem changes
- `.stignore` and `memory-policy.md` are planned/created
- valid shared skills are detected
- invalid directories without `SKILL.md` are ignored
- existing local skill directories are backed up, not deleted
- links are created only with `--apply`
- `doctor` detects Syncthing conflict files

Real user paths are not touched by `self-test`.

## Current Project Files

Create:

```text
codex_shared_onboard.py
docs/superpowers/specs/2026-04-29-codex-shared-onboarding-design.md
docs/superpowers/plans/2026-04-29-codex-shared-onboarding.md
```

No repository commit is required unless this folder becomes a Git repository.
