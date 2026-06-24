# idi-ftm2j-shared

Shared AWS infrastructure for the FTM2J terminal ecosystem. Two independent Pulumi stacks — deploy bootstrap first, then shared.

---

## `pulumi-bootstrap` — GitHub Actions OIDC

Provisions the account-level OIDC identity provider and the two IAM roles that all `dsi-clinic` repos use to authenticate with AWS from GitHub Actions.

> **Run locally.** This stack must be deployed from a workstation with AWS credentials — it creates the very roles that CI uses, so CI cannot deploy it itself.

```bash
cd pulumi-bootstrap
pulumi stack select dev
pulumi preview
pulumi up
```

**Roles created:**

| Role | Assumed by | Access |
|------|-----------|--------|
| `checks` | Pull requests, manual `workflow_dispatch` runs | Read-only (`pulumi preview`) |
| `deploy` | Pushes to `main`, `dev`, `release/**` | Full deploy (`pulumi up`) |

Both roles trust any repository in the `dsi-clinic` org — no updates needed when new repos are added.

---

## `pulumi` — Shared Infrastructure

Provisions the AWS resources shared across all FTM2J processor pipelines. Individual processor stacks reference these outputs rather than creating their own copies.

```bash
cd pulumi
pulumi stack select dev
pulumi preview
pulumi up
```

**Resources:**

| Resource | Description |
|----------|-------------|
| S3 bucket | Pipeline input, output, and failure storage. Encrypted at rest; retained on stack destroy to prevent data loss. |
| S3 VPC gateway endpoint | Routes S3 traffic over the private AWS network, avoiding internet egress from ECS tasks. |
| SQS dead-letter queue | Captures EventBridge Scheduler invocation failures for inspection and replay. |

**Stack outputs** consumed by downstream processor stacks:

```
processor_bucket_name
processor_bucket_arn
s3_endpoint_id
s3_endpoint_arn
dlq_url
dlq_arn
```

>`deploy.yml` is path-filtered: version/publish jobs only run when `src/**` or `pyproject.toml` changed; the Pulumi deploy job only runs when `pulumi-shared/**` changed.

---

# development + contributing

Install all dependency groups (includes `dev` tools: pytest, ruff):

```bash
uv sync --all-groups
```

## tests

```bash
uv run pytest
```

## linting + formatting

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
```

## code style

| Rule | Value |
|---|---|
| Line length | 100 characters |
| Docstring convention | Google (`pydocstyle`) |
| Type annotations | Required on all public functions and classes |
| String quotes | Double-quoted (ruff `Q` ruleset) |

## branching strategy + versioning

Two-branch model with short-lived issue branches.

### long-lived branches

| Branch | Purpose      | Version style                | Deploy target |
| ------ | ------------ | ---------------------------- | ------------- |
| `dev`  | Integration  | `X.Y.Z-alphaN` (pre-release) | `dev` stack   |
| `main` | Production   | `X.Y.Z` (stable)             | `prod` stack  |

Both branches are protected. All changes occur via pull request.

### short-lived branches

- **`issue-<number>-<slug>`** — feature, bug-fix, and chore work.
    - Branch from `dev`, PR back to `dev`.
    - While the PR is open, only [`checks.yml`](../.github/workflows/checks.yml) runs (lint, tests, security, Pulumi preview). Pushes to the issue branch do not bump the version or deploy.
    - On merge, the push to `dev` triggers [`deploy.yml`](../.github/workflows/deploy.yml): bumps the alpha version and deploys the `dev` stack.
    - Note: It is best to create branches with this naming convention as you will be able to manually deploy these branches for testing in the `dev` stack. See (#manual-deploys)
- **Hotfix** — urgent production fix.
    - Branch from `main` as `issue-<number>-hotfix-<slug>`, PR back to `main`.
    - After release, merge `main` back into `dev` (see [Syncing main back into dev](#3-syncing-main-back-into-dev)).

### ci/cd pipelines

Validation and deployment are split across two workflows:

- [`checks.yml`](../.github/workflows/checks.yml) — runs on every PR, required before merge. Lint, tests, security scan, Pulumi preview.
- [`deploy.yml`](../.github/workflows/deploy.yml) — runs on push to `dev` or `main` (i.e. after a merge). Computes/bumps version, tags, releases, deploys Pulumi, publishes to PyPI (`main` only, if the repo includes a package).

### versioning

The committed version in `pyproject.toml` is always a **stable** release. Alpha versions are never committed — they are computed inside the `dev` deploy run and used only for that run's tag/release. Only `main` writes a version back to the repo.

| Trigger        | What happens                                                                                    | Example                  |
| -------------- | ----------------------------------------------------------------------------------------------- | ------------------------ |
| Push to `dev`  | Alpha computed in-workflow (not committed): next-patch base + run number + short SHA            | `0.1.6a123+abc1234`      |
| Push to `main` | `uv version --bump patch` bumps the committed stable version, committed by the deploy bot | `0.1.5` → `0.1.6`        |

The alpha base is `uv version --bump patch --dry-run --short` (the next stable target), with `a<run-number>` for ordering and `+<short-sha>` for traceability. The `+<sha>` local segment is fine because alphas are never published to PyPI. Because the committed version is already stable, `main` uses `--bump patch` (not `--bump stable`, which would be a no-op).

**Loop prevention.** The bump (`main`) and sync-back (`dev`) commits are committed as `ftm2j-deploy-bot`, and `deploy.yml`'s `version`/`deploy-pulumi` jobs skip whenever the run's head commit was committed by that bot. This breaks the deploy→commit→deploy cycle **without** `[skip ci]` — which is deliberately avoided because it suppresses *all* workflows for the commit (including the required PR checks, silently blocking dev→main PRs) and trips on the literal string appearing anywhere in a message.

Each successful deploy:

1. **`main` only:** commits the bumped `pyproject.toml` + `uv.lock` (as the deploy bot, so it doesn't re-trigger `deploy.yml`). `dev` commits nothing.
2. Pushes a `vX.Y.Z[aN][+sha]` git tag.
3. Creates a GitHub Release — pre-release on `dev`, stable on `main`.
4. On `main`: builds the wheel/sdist and publishes to PyPI (if the repo ships a package), then the `sync-dev` job merges `main` back into `dev` (see below).

### development cycle

#### 1. dev → issue → alpha release

```
                                    PR
issue-123-add-feature  ────────────────────────────────► dev
        ▲                                              │
        │ branch                                       │ push triggers deploy.yml
        │                                              ▼
       dev ◄──────────────────────────────────── 0.1.6a123+abc1234, ...
                        merge                    deployed to dev stack
                                                 (alpha not committed)
```

1. `git switch dev && git pull`
2. `git switch -c issue-123-add-feature`
3. Commit, push, open PR targeting `dev`. `checks.yml` runs.
4. Merge the PR (squash recommended). The push to `dev` triggers `deploy.yml`:
   - Computes an alpha version in-workflow (e.g. `0.1.6a123+abc1234`) — **nothing is committed back to `dev`**.
   - Tags, creates a pre-release, deploys the `dev` Pulumi stack, publishes the image. PyPI publish is skipped.
5. Each merge into `dev` produces a fresh alpha keyed to its run number and commit SHA. They all target the same next-patch base (e.g. `0.1.6a124+def5678`, `0.1.6a125+...`) until a stable release on `main` advances the base.

#### 2. dev → main → stable release

```
dev (0.1.6a*) ───────── PR ─────────► main
 ▲                                      │ push triggers deploy.yml
 │                                      ▼
  ◄────────────────────────────────── 0.1.6 (stable)
              sync-dev job            deployed to prod stack
              (automatic)             published to PyPI
```

1. When `dev` is ready to ship, open a PR from `dev` → `main`. `checks.yml` runs against the `prod` Pulumi stack preview. (Because alphas are no longer committed to `dev`, the PR head is a normal commit and the required checks run.)
2. Review and merge. **Do not squash** — preserve history so release notes capture every change. A merge commit is fine.
3. The push to `main` triggers `deploy.yml`:
   - `uv version --bump patch` advances the committed stable version (`0.1.5` → `0.1.6`) and commits it to `main` as the deploy bot.
   - Tags `v0.1.6`, creates a stable GitHub Release, deploys the `prod` Pulumi stack, publishes to PyPI (if applicable).

#### 3. syncing main back into dev

This is **automatic**: after a stable release, the `sync-dev` job in `deploy.yml` merges `main` back into `dev` (a direct push, committed by the deploy bot) so `dev`'s `pyproject.toml` reflects the released stable version. Because the push is from `ftm2j-deploy-bot`, `deploy.yml`'s guards skip it and it doesn't re-trigger Deploy on `dev`.

> The `sync-dev` push requires `DEPLOY_KEY` to be allow-listed in `dev`'s branch protection.

If you ever need to sync manually (e.g. a hotfix landed directly on `main`):

```bash
git switch main && git pull
git switch dev && git pull
git merge main          # bring in the stable bump commit + any hotfixes
git push
```

The next push to `dev` then produces an alpha targeting the following patch (e.g. `0.1.7a*`) above the just-released `0.1.6`. On a `pyproject.toml` conflict, keep `main`'s stable version.

### manual deploys

`deploy.yml` accepts `workflow_dispatch`:

- From `dev` it deploys the `dev` stack.
- From `main` it deploys the `prod` stack.

Use this to redeploy Pulumi without a code change (e.g. after rotating a secret). Version/publish jobs stay gated on `src/**` changes.

### summary

- `dev` is the only place new work lands; every merge produces an alpha.
- `main` cuts stable releases from whatever alpha `dev` is on.
- After every release on `main`, merge `main` back into `dev`.

## branch protection rules

- Default branch is set to `dev`
- There are two rulesets: `dev` and `main`
- Deploy keys are added to the bypass list and set to "Always allow"
  - To set this up:
    1. Create an SSH key pair `ssh-keygen -t ed25519 -C "deploy key for <repo>" -f ~/.ssh/<repo>_deploy_key -N ""`
    2. Add the private key as a repository secret under `DEPLOY_KEY`
    3. Create a new Deploy Key in the repository settings with the public key content
- The branch targeting criteria is either set to: `dev` or `main`
- ✅ Restrict deletions
- ✅ Require a pull request before mergining
- ✅ Require status checkts to pass: Lint, Test, Security, Pulumi Preview
- ✅ Block force pushes
- ✅ Require code scanning results; set to CodeQL security alerts "High or higher"
