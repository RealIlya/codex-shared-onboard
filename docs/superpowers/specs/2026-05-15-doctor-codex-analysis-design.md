# Doctor Codex Analysis Design

## Goal

Add an opt-in `doctor --codex` mode that asks Codex CLI to explain the captured `doctor` diagnostics and then exits.

The feature is diagnostic only. It must not turn `doctor` into an automatic fixer, must not modify `.codex` or `.codex-shared`, and must not persist a Codex session while producing the explanation.

## User Interface

Extend the existing `doctor` command with these flags:

```bash
python codex_shared_onboard.py doctor --codex
python codex_shared_onboard.py doctor --codex --codex-only
python codex_shared_onboard.py doctor --codex --codex-read-repo
python codex_shared_onboard.py doctor --codex --codex-profile PROFILE
python codex_shared_onboard.py doctor --codex --codex-model MODEL
```

Default `doctor --codex` output:

```text
<normal doctor output>

--- Codex analysis ---
<codex exec final answer>
```

`--codex-only` suppresses the raw doctor output and prints only the Codex analysis.

## Codex Invocation

The script runs Codex non-interactively:

```bash
codex exec --ephemeral --sandbox read-only --ask-for-approval never -
```

Required properties:

- `--ephemeral` is always used so the analysis does not persist a session file.
- `--sandbox read-only` is always used so the analysis cannot write files.
- `--ask-for-approval never` is always used so the non-interactive command cannot block on prompts.
- The prompt is passed through stdin using `-`.
- If `--codex-profile PROFILE` is set, pass `-p PROFILE`.
- If `--codex-model MODEL` is set, pass `-m MODEL`.
- If `--codex-read-repo` is set, pass `-C <repository root>`.

Without `--codex-read-repo`, Codex receives only the captured diagnostics and the built-in analysis prompt.

## Prompt Contract

The stdin payload contains:

- a short instruction that this is a read-only diagnostic analysis for `codex-shared-onboard`
- the captured `doctor` output
- instructions to distinguish facts from inferences
- instructions to provide practical next steps
- instructions not to claim hidden state or propose destructive changes without explicit user approval

The prompt should ask for concise output in the same language as the surrounding CLI text when possible. The implementation may keep the prompt in English because the diagnostics are technical and path-heavy.

## Data Flow

1. `doctor` diagnostics are generated into an in-memory buffer instead of being printed directly.
2. The script decides whether to print the raw diagnostics based on `--codex-only`.
3. The script calls `codex exec` with the captured diagnostics in stdin.
4. Codex stdout is printed to the script stdout.
5. Codex stderr is printed to the script stderr.
6. The command exits after the Codex subprocess finishes.

## Exit Codes

The command returns non-zero when:

- the underlying `doctor` logic reports errors
- `codex` is missing from `PATH`
- `codex exec` exits non-zero

Warnings from `doctor` do not make the command fail, matching current `doctor` behavior.

If both `doctor` and `codex exec` fail, the returned code only needs to be non-zero; preserving both error messages is more important than distinguishing failure codes.

## Error Handling

If `codex` is missing, print a clear error:

```text
[ERROR] codex executable is missing; install Codex CLI or remove --codex.
```

If `codex exec` fails, print stderr and report the exit code. Do not retry with less restrictive sandboxing.

If `--codex-only` is used and `codex exec` fails, still print enough context to diagnose that the failure came from the Codex analysis step.

## Testing Strategy

Extend `self-test` with a fake Codex executable on `PATH`.

Tests should cover:

- normal `doctor` output is unchanged without `--codex`
- `doctor --codex` sends captured doctor output to the fake Codex process
- `doctor --codex` prints raw doctor output, a separator, and fake Codex output
- `doctor --codex --codex-only` suppresses raw doctor output
- fake Codex non-zero exit makes the command return non-zero
- the fake Codex receives `--ephemeral`, `--sandbox read-only`, and `--ask-for-approval never`

The tests must not invoke the real Codex CLI and must not touch real `.codex` paths.

## Non-Goals

- no automatic fixes
- no persistent Codex sessions
- no interactive TUI launch
- no writes to `.codex`, `.codex-shared`, or the repository from the Codex analysis mode
- no README roadmap entry for this future behavior
