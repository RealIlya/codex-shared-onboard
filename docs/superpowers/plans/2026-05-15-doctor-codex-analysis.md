# Doctor Codex Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `doctor --codex` so the CLI can pipe captured diagnostics into `codex exec` for read-only, ephemeral analysis.

**Architecture:** Keep `doctor` diagnostics as the source of truth by rendering them into an in-memory buffer. Add a small Codex analysis layer that builds a safe `codex exec` command, passes the buffered diagnostics through stdin, and prints the returned analysis. Keep tests inside the existing `self-test` harness with a fake Codex executable on `PATH`.

**Tech Stack:** Python standard library (`argparse`, `contextlib`, `io`, `os`, `subprocess`, `sys`, `pathlib`), existing single-file CLI, Markdown README docs.

---

### Task 1: Add Doctor Output Capture and Codex CLI Options

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add `doctor` subparser options**

In `build_parser()`, replace the bare doctor parser:

```python
sub.add_parser("doctor", help="Diagnose shared Codex setup.")
```

with:

```python
doctor = sub.add_parser("doctor", help="Diagnose shared Codex setup.")
doctor.add_argument("--codex", action="store_true", help="Ask Codex CLI to explain the captured diagnostics.")
doctor.add_argument("--codex-only", action="store_true", help="Print only Codex analysis, suppressing raw doctor output.")
doctor.add_argument("--codex-read-repo", action="store_true", help="Allow Codex analysis to read this repository in read-only mode.")
doctor.add_argument("--codex-profile", default=None, help="Codex config profile to pass to 'codex exec'.")
doctor.add_argument("--codex-model", default=None, help="Codex model to pass to 'codex exec'.")
doctor.add_argument("--codex-extra-prompt", default=None, help="Extra instructions appended to the Codex analysis prompt.")
```

- [ ] **Step 2: Split doctor diagnostics from the command wrapper**

Rename `command_doctor(ctx: Context)` to:

```python
def print_doctor_diagnostics(ctx: Context) -> int:
    ...
```

Keep its body unchanged except for the function name.

Add a wrapper below it:

```python
def command_doctor(ctx: Context, args: argparse.Namespace) -> int:
    return print_doctor_diagnostics(ctx)
```

- [ ] **Step 3: Update main dispatch**

In `main()`, replace:

```python
if args.command == "doctor":
    return command_doctor(ctx)
```

with:

```python
if args.command == "doctor":
    return command_doctor(ctx, args)
```

- [ ] **Step 4: Run a quick compatibility check**

Run:

```bash
python codex_shared_onboard.py doctor
```

Expected: output shape matches the previous `doctor` command and the command exits `0` on warning-only diagnostics.

### Task 2: Implement Codex Analysis Invocation

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add prompt constants and helpers**

Add these definitions near the other top-level constants/helpers:

```python
CODEX_ANALYSIS_SEPARATOR = "--- Codex analysis ---"


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
        "- memory linking state",
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
```

- [ ] **Step 2: Add Codex command builder**

Add:

```python
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
```

- [ ] **Step 3: Add subprocess runner**

Add:

```python
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
```

- [ ] **Step 4: Wire the wrapper**

Replace the simple wrapper from Task 1 with:

```python
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
```

- [ ] **Step 5: Run syntax check**

Run:

```bash
python -m py_compile codex_shared_onboard.py
```

Expected: exits `0`.

### Task 3: Extend Self-Test with Fake Codex

**Files:**
- Modify: `codex_shared_onboard.py`

- [ ] **Step 1: Add a fake executable writer**

Inside `command_self_test()`, before the first `doctor` assertion block, create a fake Codex executable directory:

```python
        fake_bin = case_dir / "fake-bin"
        fake_bin.mkdir()
        fake_codex_log = case_dir / "fake-codex-log.json"
        fake_codex_stdin = case_dir / "fake-codex-stdin.txt"
        fake_codex_fail = case_dir / "fake-codex-fail"
```

Create `codex.cmd` on Windows and `codex` otherwise. The fake script must:

- record argv into `fake-codex-log.json`
- record stdin into `fake-codex-stdin.txt`
- print `FAKE CODEX ANALYSIS`
- exit `7` when `fake-codex-fail` exists

- [ ] **Step 2: Run `doctor --codex` with fake PATH**

Add helper inside `command_self_test()`:

```python
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
```

Run:

```python
        codex_result = run_script_with_fake_codex([
            "--codex-dir",
            str(codex_dir),
            "--shared-dir",
            str(shared_dir),
            "doctor",
            "--codex",
            "--codex-extra-prompt",
            "Answer in Russian.",
        ])
```

Assert:

```python
        assert_true(codex_result.returncode == 0, "doctor --codex should return 0 with fake Codex")
        assert_true(CODEX_ANALYSIS_SEPARATOR in codex_result.stdout, "doctor --codex should print analysis separator")
        assert_true("FAKE CODEX ANALYSIS" in codex_result.stdout, "doctor --codex should print fake Codex output")
        assert_true("platform=" in codex_result.stdout, "doctor --codex should print raw doctor output by default")
```

- [ ] **Step 3: Assert fake Codex argv and stdin**

Read the fake log and assert:

```python
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
```

- [ ] **Step 4: Assert `--codex-only`**

Run `doctor --codex --codex-only` with fake Codex and assert:

```python
        codex_only_result = run_script_with_fake_codex([
            "--codex-dir",
            str(codex_dir),
            "--shared-dir",
            str(shared_dir),
            "doctor",
            "--codex",
            "--codex-only",
        ])
        assert_true(codex_only_result.returncode == 0, "doctor --codex-only should return 0 with fake Codex")
        assert_true("FAKE CODEX ANALYSIS" in codex_only_result.stdout, "doctor --codex-only should print analysis")
        assert_true("platform=" not in codex_only_result.stdout, "doctor --codex-only should suppress raw doctor output")
```

- [ ] **Step 5: Assert Codex failure returns non-zero**

Run `doctor --codex --codex-only --codex-read-repo --codex-profile profile-test --codex-model model-test` with fake Codex and assert `-C`, `-p`, and `-m` are forwarded.

Create `fake-codex-fail`, run `doctor --codex-only`, and assert:

```python
        fake_codex_fail.write_text("fail\n", encoding="utf-8", newline="\n")
        failing_codex_result = run_script_with_fake_codex([
            "--codex-dir",
            str(codex_dir),
            "--shared-dir",
            str(shared_dir),
            "doctor",
            "--codex",
            "--codex-only",
        ])
        assert_true(failing_codex_result.returncode != 0, "doctor --codex should fail when codex exec fails")
        assert_true("codex exec failed with exit code" in failing_codex_result.stdout, "doctor --codex should report Codex exit code")
        fake_codex_fail.unlink()
```

- [ ] **Step 6: Run self-test**

Run:

```bash
python codex_shared_onboard.py self-test
```

Expected: `self-test: PASS`.

### Task 4: Update README Command Documentation

**Files:**
- Modify: `README.md`
- Modify: `README.ru.md`

- [ ] **Step 1: Update command options in English README**

Add these lines under the existing command options block after `install --configure-syncthing`:

```text
doctor --codex             Ask Codex CLI to explain captured diagnostics.
doctor --codex-only        Print only Codex analysis, suppressing raw doctor output.
doctor --codex-read-repo   Allow read-only repository inspection during Codex analysis.
doctor --codex-profile P   Pass a Codex config profile to codex exec.
doctor --codex-model M     Pass a Codex model to codex exec.
doctor --codex-extra-prompt TEXT
                            Append extra instructions to the Codex analysis prompt.
```

- [ ] **Step 2: Add English example**

Add to dry-run examples:

```bash
python codex_shared_onboard.py doctor --codex
python codex_shared_onboard.py doctor --codex --codex-extra-prompt "Answer in Russian."
```

- [ ] **Step 3: Update Russian README**

Add the same command options in Russian, including:

```text
doctor --codex             Попросить Codex CLI объяснить captured diagnostics.
doctor --codex-only        Печатать только Codex analysis без raw doctor output.
doctor --codex-read-repo   Разрешить read-only чтение репозитория во время Codex analysis.
doctor --codex-profile P   Передать Codex config profile в codex exec.
doctor --codex-model M     Передать Codex model в codex exec.
doctor --codex-extra-prompt TEXT
                            Добавить дополнительные инструкции в Codex analysis prompt.
```

Add examples:

```bash
python codex_shared_onboard.py doctor --codex
python codex_shared_onboard.py doctor --codex --codex-extra-prompt "Answer in Russian."
```

### Task 5: Final Verification and Commit

**Files:**
- Modify: `codex_shared_onboard.py`
- Modify: `README.md`
- Modify: `README.ru.md`
- Create: `docs/superpowers/plans/2026-05-15-doctor-codex-analysis.md`

- [ ] **Step 1: Run verification**

Run:

```bash
python -m py_compile codex_shared_onboard.py
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py --version
git diff --check
```

Expected:

- syntax check exits `0`
- self-test prints `self-test: PASS`
- doctor exits `0` on warning-only diagnostics
- version prints the current app version
- diff check exits `0`

- [ ] **Step 2: Review diff**

Run:

```bash
git diff --stat
git diff -- codex_shared_onboard.py README.md README.ru.md docs/superpowers/plans/2026-05-15-doctor-codex-analysis.md
```

Expected: changes are limited to the implementation, docs, and this plan.

- [ ] **Step 3: Commit**

Use one feature commit:

```bash
git add codex_shared_onboard.py README.md README.ru.md docs/superpowers/plans/2026-05-15-doctor-codex-analysis.md
git commit -m "feat(doctor): add codex analysis mode" -m "- pipe captured diagnostics into ephemeral read-only codex exec
- document codex analysis flags in English and Russian
- cover codex invocation with self-test fake executable"
```
