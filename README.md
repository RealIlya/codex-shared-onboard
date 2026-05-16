# Codex Shared Onboarding

[Русская версия](README.ru.md)

`codex_shared_onboard.py` helps onboard a machine into a safe shared Codex layer:

- `.codex-shared` is synced between machines with Syncthing.
- `.codex` is not synced as a whole.
- User skills are linked from `.codex-shared/skills-user` into local `.codex/skills`.
- Codex memories can be published into `.codex-shared/memories-published/current` and consumed as local copies.
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
doctor                      Diagnose paths, tools, shared files, local/published memory status, conflicts, and skill links.
snapshot                    Create a local Git snapshot of .codex-shared.
memories adopt              DEPRECATED: copy local .codex/memories into .codex-shared/memories and link local memories to the shared directory.
memories link               DEPRECATED: connect local .codex/memories to an existing .codex-shared/memories without copying local reader memories over shared memories.
memories publish            Copy local .codex/memories into .codex-shared/memories-published/current and keep a snapshot.
memories consume            Replace local .codex/memories with a validated copy of .codex-shared/memories-published/current.
install-cli                 Install a local codex-shared-onboard launcher.
version                     Print the tool version.
self-test                   Run the script's temporary-directory test suite.
```

Command options:

```text
install --apply             Apply install changes. Without it, print the planned changes only.
install --configure-syncthing
                            Try to register .codex-shared in local Syncthing through the REST API.

doctor --codex             Ask Codex CLI to explain captured diagnostics.
doctor --codex-only        Print only Codex analysis, suppressing raw doctor output.
doctor --codex-read-repo   Allow read-only repository inspection during Codex analysis.
doctor --codex-profile P   Pass a Codex config profile to codex exec.
doctor --codex-model M     Pass a Codex model to codex exec.
doctor --codex-extra-prompt TEXT
                            Append extra instructions to the Codex analysis prompt.

snapshot --apply            Initialize/use Git in .codex-shared and commit the current shared state.

memories adopt --apply      DEPRECATED: apply writer adoption. Refuses to overwrite existing .codex-shared/memories.
memories link --apply       DEPRECATED: apply reader/shared memory linking. Refuses to continue on memory conflict files.
memories publish --apply    Apply copy-based writer publish. Refuses local memory conflict files.
memories consume --apply    Apply copy-based reader consume. Requires a valid manifest.json.

install-cli --apply         Write the launcher. Without it, print the planned changes only.
install-cli --bin-dir PATH  Directory for the launcher. Defaults to ~/.local/bin.
install-cli --force         Overwrite an existing launcher when its content differs.
install-cli --no-path-update
                            On Windows, do not add the launcher directory to the user's PATH.
```

Dry-run examples:

```bash
python codex_shared_onboard.py install
python codex_shared_onboard.py doctor --codex
python codex_shared_onboard.py doctor --codex --codex-extra-prompt "Answer in Russian."
python codex_shared_onboard.py memories publish
python codex_shared_onboard.py memories consume
python codex_shared_onboard.py snapshot
```

Apply examples:

```bash
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories publish --apply
python codex_shared_onboard.py memories consume --apply
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

Local `.codex` consumes shared skills through links:

```text
~/.codex/skills/<skill> -> ~/.codex-shared/skills-user/<skill>
```

The preferred memories model is copy-based:

```text
Writer:
  ~/.codex/memories -> publish copy -> ~/.codex-shared/memories-published/current

Reader:
  ~/.codex-shared/memories-published/current -> consume copy -> ~/.codex/memories
```

This keeps active `~/.codex/memories` local on every machine. Published copies include Codex-owned memory internals such as `.git`, `.agents`, and `.codex`, plus a `manifest.json` with file hashes.

Legacy memory linking is still available through `memories adopt` and `memories link`:

```text
~/.codex/memories -> ~/.codex-shared/memories
```

Use the legacy whole-directory link only when that tradeoff is explicit. On WSL/Linux, a whole-directory symlink crossing into `/mnt/c` can trigger Codex sandbox/bubblewrap issues.

## First Machine / Writer

On the primary machine that already has the memories you want to share:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories publish
python codex_shared_onboard.py memories publish --apply
```

`memories publish --apply` does this:

- Requires local `.codex/memories` to be a real directory, not a symlink/junction.
- Refuses conflict-like memory files.
- Copies local `.codex/memories` into `.codex-shared/memories-published/current`.
- Writes `manifest.json` with file sizes and SHA-256 hashes.
- Keeps a timestamped copy under `.codex-shared/memories-published/snapshots/`.

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

On a new machine, configure Syncthing first and wait until `.codex-shared` has arrived, including `.codex-shared/memories-published/current`.

Then run:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories consume
python codex_shared_onboard.py memories consume --apply
```

`memories consume --apply` does this:

- Verifies `.codex-shared/memories-published/current/manifest.json`.
- Refuses conflict-like published memory files.
- Backs up local `.codex/memories` if it exists.
- Replaces local `.codex/memories` with a real copied directory, not a shared link.

For a reader machine, use this in `~/.codex/config.toml`:

```toml
[features]
memories = true

[memories]
use_memories = true
generate_memories = false
```

This lets the reader use memories without generating new memory state.

## Legacy Memory Links

`memories adopt` and `memories link` are deprecated and kept only for existing whole-directory link setups.

Use `memories adopt --apply` on the original writer only when you want `.codex/memories` to become a link to `.codex-shared/memories`.

Use `memories link --apply` on a reader only when `.codex-shared/memories` already exists and you explicitly accept the symlink/junction model.

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
*sync_conflict*
*conflicted copy*
delimiter-separated conflict/conflicted markers
```

If conflicts exist:

- Do not silently pick a winner.
- Do not auto-merge `MEMORY.md`, `memory_summary.md`, or `raw_memories.md`.
- Create a snapshot first.
- Then inspect diffs manually and decide what to keep.

## Recovery

To roll back from consumed memories:

1. Close Codex CLI.
2. Rename the current `.codex/memories` out of the way.
3. Rename the desired backup back to `.codex/memories`.

Example backup:

```text
~/.codex/memories.bak-local-YYYYMMDD-HHMMSS
```

For legacy linked memories, remove the junction/symlink itself, not the target `.codex-shared/memories`.

## Important Limits

- The script does not sync `.codex` as a whole.
- The script does not install Codex CLI, Python, or Syncthing.
- The script does not approve remote Syncthing devices.
- The script does not edit `config.toml` automatically.
- The script does not choose writer/reader memory mode automatically.
- The script does not merge memory conflicts.
