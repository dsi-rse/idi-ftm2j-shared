# Agent Instructions

Guidance for agents working in `idi-ftm2j-shared`. For human-facing detail (full
deployment flow, Pulumi resources), see [`README.md`](README.md).

## Project overview

`idi-ftm2j-shared` provides the shared building blocks for the **FTM2J** processor
ecosystem. It has two distinct parts in one repo:

1. **Python package** — [`src/idi_ftm2j_shared/`](src/idi_ftm2j_shared): runtime
   utilities used by individual FTM2J processors (S3 storage helpers, CloudWatch
   logging via `watchtower`, SEC scraping, failure handling, API client). Published
   to PyPI as `idi-ftm2j-shared`.
2. **Pulumi infrastructure** — two independent programs:
   - [`pulumi-bootstrap/`](pulumi-bootstrap): account-level GitHub OIDC provider and
     the `checks`/`deploy` IAM roles all `dsi-clinic` repos assume from CI. Deployed
     **manually from a workstation** (it creates the roles CI itself uses).
   - [`pulumi-shared/`](pulumi-shared): shared AWS resources (S3 bucket, S3 VPC
     gateway endpoint, SQS dead-letter queue) that downstream processor stacks
     reference via stack outputs. Deployed by CI.

## Repository layout

```
src/idi_ftm2j_shared/   # published Python package (runtime utilities)
tests/                  # pytest suite
pulumi-shared/          # Pulumi program: shared infra (__main__.py + infra/)
pulumi-bootstrap/       # Pulumi program: OIDC + IAM roles (run locally)
.github/workflows/      # checks.yml (PR gate) + deploy.yml (post-merge)
```

## Environment & tooling

- **Python 3.13** (`requires-python = ">=3.13"`).
- **`uv`** manages the environment, dependencies, versioning, and builds — use it for
  everything; do not call `pip`/`python`/`pytest` directly outside `uv run`.
- Build backend: **hatchling**.
- Dependency groups (in `pyproject.toml`): `deploy` (ruff, pytest, pytest-cov,
  pytest-mock, pip-audit), `pulumi` (pulumi, pulumi-aws), `test` (moto).

## Common commands

```bash
uv sync --all-groups        # install everything (package + all dep groups)
uv run pytest               # run the test suite
uv run pytest --cov=idi_ftm2j_shared   # with coverage
uv run ruff check .         # lint
uv run ruff format .        # format
uv run ruff format --check . # CI-style format check (no writes)
```

`checks.yml` runs lint, tests, a security scan (`pip-audit` + CodeQL), and a Pulumi
preview on every PR. Run `ruff check`, `ruff format`, and `pytest` locally before
pushing to keep the gate green.

## Code style

| Rule | Value |
|---|---|
| Line length | 100 |
| Docstring convention | Google (`pydocstyle`) |
| Type annotations | Required on all public functions and classes (ruff `ANN`) |
| String quotes | Double-quoted (ruff `Q`) |
| Imports | Sorted by ruff `I` |

Active ruff rulesets include `ANN, B, C4, D, I, N, PD, PLR2004, PTH, Q, S, UP, YTT`.
Tests relax annotation/docstring rules (see `[tool.ruff.lint.per-file-ignores]`).
Prefer `pathlib` over `os.path` (`PTH`), and avoid magic numbers (`PLR2004`).

## CI/CD & versioning

- **`checks.yml`** — required PR gate (lint, test, security, Pulumi preview).
- **`deploy.yml`** — runs on push to `dev`/`main` after a merge; versions, tags,
  releases, deploys Pulumi, publishes to PyPI (`main` only).
- **Versioning model:** the committed `pyproject.toml` version is always **stable**.
  Alpha versions are computed inside the `dev` deploy run (`<next-patch>a<run>+<sha>`)
  and are **never committed**. `main` bumps with `uv version --bump patch` and the
  `sync-dev` job merges `main` back into `dev`. The bump/sync commits are made as
  `idi-deploy-bot`; Deploy's jobs skip bot-authored head commits to break the
  deploy→commit→deploy loop (instead of `[skip ci]`, which would also suppress the
  required PR checks).

See [`README.md`](README.md) (“branching strategy + versioning”) for the full flow,
diagrams, and the manual-deploy / hotfix paths. **Do not** reintroduce alpha-version
commits to `dev` — that previously suppressed the required dev→main PR checks.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var
