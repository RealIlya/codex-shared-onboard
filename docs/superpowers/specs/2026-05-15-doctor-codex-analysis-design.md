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
python codex_shared_onboard.py doctor --codex --codex-extra-prompt TEXT
```

Default `doctor --codex` output:

```text
<normal doctor output>

--- Codex analysis ---
<codex exec final answer>
```

`--codex-only` suppresses the raw doctor output and prints only the Codex analysis.

Update both user-facing READMEs during implementation:

- `README.md` must document the new `doctor --codex` flags in the commands/options sections and show one basic example.
- `README.ru.md` must document the same commands and examples in Russian.
- These README updates are command documentation, not a roadmap or future-plan section.

## Codex Invocation

The script runs Codex non-interactively:

```bash
codex --ask-for-approval never exec --ephemeral --sandbox read-only --color never --output-last-message <tempfile> -
```

Required properties:

- `--ephemeral` is always used so the analysis does not persist a session file.
- `--sandbox read-only` is always used so the analysis cannot write files.
- `--ask-for-approval never` is always used as a global Codex CLI option before `exec` so the non-interactive command cannot block on prompts.
- `--output-last-message <tempfile>` is used so the script prints only the final Codex answer on success, not the full Codex transcript/banner.
- `--color never` is used so captured failure output is plain text.
- The prompt is passed through stdin using `-`.
- If `--codex-profile PROFILE` is set, pass `-p PROFILE`.
- If `--codex-model MODEL` is set, pass `-m MODEL`.
- If `--codex-read-repo` is set, pass `-C <repository root>`.
- `--codex-extra-prompt TEXT` is appended to the stdin payload as user-provided analysis preferences.

Without `--codex-read-repo`, Codex receives only the captured diagnostics and the built-in analysis prompt.

## Prompt Contract

The stdin payload contains:

- a short instruction that this is a read-only diagnostic analysis for `codex-shared-onboard`
- the captured `doctor` output
- instructions to distinguish facts from inferences
- instructions to provide practical next steps
- instructions not to claim hidden state or propose destructive changes without explicit user approval
- optional extra user instructions from `--codex-extra-prompt`

The built-in prompt is English by default. The script does not infer the user's language. Users can request another language or format through `--codex-extra-prompt`, for example `--codex-extra-prompt "Answer in Russian."`.

## Data Flow

1. `doctor` diagnostics are generated into an in-memory buffer instead of being printed directly.
2. The script decides whether to print the raw diagnostics based on `--codex-only`.
3. The script calls `codex exec` with the captured diagnostics in stdin.
4. On success, the last Codex message is read from the temporary output file and printed to stdout.
5. On failure, Codex stdout and stderr are filtered down to error-like lines before printing, so the wrapper does not dump the full prompt/transcript.
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
- the fake Codex receives `--ephemeral`, `--sandbox read-only`, `--ask-for-approval never`, `--color never`, and `--output-last-message`
- failure output keeps Codex error lines but does not print the full failed transcript

The tests must not invoke the real Codex CLI and must not touch real `.codex` paths.

## Non-Goals

- no automatic fixes
- no persistent Codex sessions
- no interactive TUI launch
- no writes to `.codex`, `.codex-shared`, or the repository from the Codex analysis mode
- no README roadmap entry for this future behavior
