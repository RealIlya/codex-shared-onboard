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

## Command Reference

General form:

```bash
python codex_shared_onboard.py [global-options] <command> [command-options]
```

After installing the launcher, `codex-shared-onboard` can be used instead of `python codex_shared_onboard.py`.

Global options:

```text
--codex-dir PATH            Local Codex home. Defaults to ~/.codex.
--shared-dir PATH           Shared layer directory. Defaults to ~/.codex-shared, or /mnt/c/Users/<WindowsUser>/.codex-shared in WSL when detected.
--apply                     Actually change files. Without it, mutating commands run in dry-run mode.
--verbose                   Print extra diagnostic details for subprocesses and API calls.
--syncthing-url URL         Syncthing REST API URL. Defaults to http://127.0.0.1:8384.
--syncthing-api-key KEY     Syncthing API key. If omitted, the script tries to read it from local Syncthing config.
--version                   Print the tool version and exit.
-h, --help                  Show help.
```

Commands:

```text
install                     Prepare .codex-shared and link shared user skills into .codex/skills.
doctor                      Diagnose paths, tools, shared files, memory layout, conflicts, and skill links.
snapshot                    Create a local Git snapshot of .codex-shared.
memories adopt              Copy local .codex/memories into .codex-shared/memories and switch local memories to the platform-specific shared layout.
memories link               Connect local .codex/memories to an existing .codex-shared/memories without copying local reader memories over shared memories.
install-cli                 Install a local codex-shared-onboard launcher.
version                     Print the tool version.
self-test                   Run the script's temporary-directory test suite.
```

Command options:

```text
install --apply             Apply install changes. Without it, print the planned changes only.
install --configure-syncthing
                            Try to register .codex-shared in local Syncthing through the REST API.

snapshot --apply            Initialize/use Git in .codex-shared and commit the current shared state.

memories adopt --apply      Apply writer adoption. Refuses to overwrite existing .codex-shared/memories.
memories link --apply       Apply reader/shared memory linking. Refuses to continue on memory conflict files.

install-cli --apply         Write the launcher. Without it, print the planned changes only.
install-cli --bin-dir PATH  Directory for the launcher. Defaults to ~/.local/bin.
install-cli --force         Overwrite an existing launcher when its content differs.
install-cli --no-path-update
                            On Windows, do not add the launcher directory to the user's PATH.
```

Dry-run examples:

```bash
python codex_shared_onboard.py install
python codex_shared_onboard.py memories link
python codex_shared_onboard.py snapshot
```

Apply examples:

```bash
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories link --apply
python codex_shared_onboard.py snapshot --apply
```

## CLI Launcher

To install a local `codex-shared-onboard` command:

```bash
python codex_shared_onboard.py install-cli
python codex_shared_onboard.py install-cli --apply
```

By default this writes:

```text
~/.local/bin/codex-shared-onboard
```

On Windows this writes `codex-shared-onboard.cmd` and adds the bin directory to
the current user's `PATH`. Open a new terminal after installation.

On Linux/macOS, make sure `~/.local/bin` is on `PATH`, then use:

```bash
codex-shared-onboard doctor
codex-shared-onboard install --apply
```

The launcher is a small shim that points to this `codex_shared_onboard.py` file. Updating the checked-out script usually updates the installed command automatically:

```bash
git pull --ff-only
codex-shared-onboard version
```

Re-run `install-cli --apply --force` only when you moved the repository, changed the launcher target path, or need to replace a launcher with different content:

```bash
python codex_shared_onboard.py install-cli --apply --force
```

## Core Model

Do not sync `.codex` as a whole.

Sync only:

```text
~/.codex-shared
```

Local `.codex` consumes shared parts through links:

```text
~/.codex/skills/<skill> -> ~/.codex-shared/skills-user/<skill>
```

Memory linking is platform-specific:

```text
Windows:
  ~/.codex/memories -> ~/.codex-shared/memories

WSL/Linux:
  ~/.codex/memories/                  real local directory
  ~/.codex/memories/MEMORY.md         -> ~/.codex-shared/memories/MEMORY.md
  ~/.codex/memories/memory_summary.md -> ~/.codex-shared/memories/memory_summary.md
  ~/.codex/memories/raw_memories.md   -> ~/.codex-shared/memories/raw_memories.md
  ~/.codex/memories/rollout_summaries -> ~/.codex-shared/memories/rollout_summaries
  ~/.codex/memories/extensions        -> ~/.codex-shared/memories/extensions
  plus any other non-internal top-level memory artifacts
```

WSL/Linux intentionally does not link `.git`, `.agents`, or `.codex` from shared memories. A whole-directory symlink can break Codex sandboxing when shared memories contain Codex-owned internal state.

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
- Creates the platform-specific memory layout.
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
- Creates the platform-specific memory layout.
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
2. Remove the Windows `.codex/memories` junction, or move the WSL/Linux real `.codex/memories` directory aside after removing its per-entry links.
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
