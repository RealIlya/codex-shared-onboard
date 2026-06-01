# Repository Guidelines

## Project Structure & Module Organization

This repository contains a single Python CLI tool for onboarding machines into a shared Codex layer.

- `codex_shared_onboard.py` is the application source, CLI parser, filesystem logic, Syncthing API integration, and built-in self-test suite.
- `README.md` and `README.ru.md` document user-facing workflows; keep them in sync when command behavior changes.
- `docs/superpowers/` stores supporting documentation.
- There is no separate package or test directory. Temporary self-test data is created under `.onboard-test-tmp/` and cleaned up automatically.

## Build, Test, and Development Commands

Use Python 3.10+.

```bash
python3 codex_shared_onboard.py --help
```

Shows the CLI surface and should remain readable after parser changes.

```bash
python3 codex_shared_onboard.py self-test
```

Runs the temporary-directory test suite. It must not touch real `~/.codex`.

```bash
python3 -m py_compile codex_shared_onboard.py
git diff --check
```

Checks syntax and whitespace before committing.

```bash
python3 codex_shared_onboard.py doctor
```

Runs local diagnostics. It may inspect real Codex/Syncthing paths but should not mutate them.

`install-cli --apply` installs both `codex-shared-onboard` for setup and `codex-shared` for daily operator commands.

## Coding Style & Naming Conventions

Follow the existing standard-library-only Python style. Use 4-space indentation, type hints for new helpers, and descriptive snake_case names. Keep filesystem mutations behind dry-run aware helpers and require `--apply` for real changes. Prefer explicit path checks over clever abstractions.

## Testing Guidelines

Add self-test coverage for new CLI behavior before implementation when practical. Test both dry-run and `--apply` paths for mutating commands. For shared-memory behavior, include conflict/manifest failure cases and verify that failed operations leave local state untouched.

## Commit & Pull Request Guidelines

History uses short scoped commits such as `fix(memories): restore whole-directory links`, `fix(cli): add version command and portable launcher`, and PR-style feature titles. Prefer imperative, scoped messages: `feat(memories): add publish consume flow` or `fix(doctor): report published memories status`.

PRs should include a concise behavior summary, validation commands run, and notes about any `.codex` or `.codex-shared` safety implications.

## Security & Configuration Tips

Do not sync `.codex` as a whole. Treat `auth.json`, sessions, history, logs, caches, and `.system/` as local runtime state. Do not manually edit generated memory files unless explicitly requested; use the CLI mechanisms and keep dry-run output reviewable.
