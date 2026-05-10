# Codex Shared Onboarding

[Русская версия](README.ru.md)

`codex_shared_onboard.py` helps onboard a machine into a safe shared Codex layer:

- `.codex-shared` is synced between machines with Syncthing.
- `.codex` is not synced as a whole.
- User skills are linked from `.codex-shared/skills-user` into local `.codex/skills`.
- Codex memories can be linked through `.codex-shared/memories`.
- Codex runtime state stays local: `config.toml`, `auth.json`, `rules/`, `sessions/`, `history.jsonl`, `state_*.sqlite*`, `logs_*.sqlite*`, `cache/`, `tmp/`, `.tmp/`, `.system/`.

The script is dry-run by default. Real filesystem changes require `--apply`.

## Requirements

- Python 3.10+.
- Codex CLI installed.
- Syncthing, if you want cross-machine sync.
- On Windows, the script uses junctions for directories when regular symlinks are unavailable.

## Quick Check

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
```

`self-test` only uses temporary directories under the current working directory. It does not touch your real `.codex`.

## Core Model

Do not sync `.codex` as a whole.

Sync only:

```text
~/.codex-shared
```

Local `.codex` consumes shared parts through links:

```text
~/.codex/skills/<skill> -> ~/.codex-shared/skills-user/<skill>
~/.codex/memories      -> ~/.codex-shared/memories
```

## First Machine / Writer

On the primary machine that already has the memories you want to share:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories adopt
python codex_shared_onboard.py memories adopt --apply
```

`memories adopt --apply` does this:

- Copies local `.codex/memories` into `.codex-shared/memories`.
- Renames the old local `.codex/memories` to `memories.bak-local-YYYYMMDD-HHMMSS`.
- Creates a symlink or junction `.codex/memories -> .codex-shared/memories`.
- Does not delete the original memories.
- Does not overwrite an existing `.codex-shared/memories`.

For the writer machine, use this in `~/.codex/config.toml`:

```toml
[features]
memories = true

[memories]
use_memories = true
generate_memories = true
```

Prefer a single memories writer at a time.

## New Machine / Reader

On a new machine, configure Syncthing first and wait until `.codex-shared` has arrived, including `.codex-shared/memories`.

Then run:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories link
python codex_shared_onboard.py memories link --apply
```

`memories link --apply` does this:

- Verifies that `.codex-shared/memories` exists.
- Backs up local `.codex/memories` if it exists.
- Creates a symlink or junction `.codex/memories -> .codex-shared/memories`.
- Does not copy local reader memories over shared memories.

For a reader machine, use this in `~/.codex/config.toml`:

```toml
[features]
memories = true

[memories]
use_memories = true
generate_memories = false
```

This lets the reader consume shared memories without updating them.

## Syncthing

The script can try to register `.codex-shared` in local Syncthing through the REST API:

```bash
python codex_shared_onboard.py install --configure-syncthing
python codex_shared_onboard.py install --configure-syncthing --apply
```

This does not install Syncthing and does not add remote devices. Device pairing and folder sharing still happen in the Syncthing UI.

If the API key is not detected automatically:

```bash
python codex_shared_onboard.py --syncthing-api-key YOUR_KEY install --configure-syncthing --apply
```

## Shared Skills

Shared skills live here:

```text
~/.codex-shared/skills-user/<skill-name>/SKILL.md
```

After:

```bash
python codex_shared_onboard.py install --apply
```

they become available to local Codex through:

```text
~/.codex/skills/<skill-name>
```

If a local skill with the same name already exists as a real directory,
`install --apply` preserves it under:

```text
~/.codex/skills-backups/<skill-name>.bak-local-YYYYMMDD-HHMMSS
```

Backups are kept outside `~/.codex/skills` so Codex does not register them as
duplicate active skills.

`.system` skills are not shared. They are local to each Codex installation.

## Snapshot

You can create a local Git snapshot of `.codex-shared`:

```bash
python codex_shared_onboard.py snapshot
python codex_shared_onboard.py snapshot --apply
```

The script does not push. The snapshot is a local recovery point before manual edits or conflict resolution.

## Memory Conflicts

Syncthing cannot semantically merge generated memories.

Before touching shared memories, the script checks for conflict-like files:

```text
*sync-conflict*
*conflict*
```

If conflicts exist:

- Do not silently pick a winner.
- Do not auto-merge `MEMORY.md`, `memory_summary.md`, or `raw_memories.md`.
- Create a snapshot first.
- Then inspect diffs manually and decide what to keep.

## Recovery

To roll back from shared memories:

1. Close Codex CLI.
2. Remove the `.codex/memories` junction or symlink.
3. Rename the desired backup back to `.codex/memories`.

Example backup:

```text
~/.codex/memories.bak-local-YYYYMMDD-HHMMSS
```

On Windows, remove the junction itself, not the target `.codex-shared/memories`.

## Important Limits

- The script does not sync `.codex` as a whole.
- The script does not install Codex CLI, Python, or Syncthing.
- The script does not approve remote Syncthing devices.
- The script does not edit `config.toml` automatically.
- The script does not choose writer/reader memory mode automatically.
- The script does not merge memory conflicts.
