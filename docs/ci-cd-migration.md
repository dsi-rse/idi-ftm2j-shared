# CI/CD shared-workflow migration runbook

Companion to GitHub issue [#25](https://github.com/dsi-clinic/idi-ftm2j-shared/issues/25)
and beads epic `idi-ftm2j-shared-6if`. The shared reusable workflows and the host
repo's own callers are already implemented (`.github/`). This document covers the
steps that need **values held in GitHub secrets** or **org/repo admin access** —
i.e. the parts that can't be committed as code from a clone.

Org is assumed to be `dsi-clinic`. Adjust commands to your `gh` auth/admin scope.

---

## Architecture recap (where every value lives)

| Store | Holds | Why |
|---|---|---|
| **GitHub** (org/repo/env) | bootstrap creds to *reach* AWS/Pulumi/git, prod gate | only thing that must exist before Pulumi runs |
| **Pulumi StackReference** | shared non-secret values (bucket/DLQ) | one producer, many consumers — issue `.14` |
| **AWS SSM `SecureString`** | genuine API-key secrets | repos are public; keeps secrets out of git + state — issue `.15` |
| **Committed `Pulumi.<stack>.yaml`** | all non-secret per-processor knobs | git audit trail / `git revert` — issue `.16` (this doc) |

`idi:app_name` is **not** committed — the workflow sets it from the caller input.

---

## `.16` — committed `Pulumi.<stack>.yaml` (non-secret knobs)

### Host repo (done)

`pulumi-shared/Pulumi.dev.yaml` and `Pulumi.prod.yaml` are committed with
`idi:bucket_name` + `idi:dlq_retention_days`. The bucket name resolves to
`{project}-{stack}-{app_name}-{bucket_name}` (`infra/config.py`), so dev/prod are
already distinct with the same config value.

### Siblings (templates — fill values from each repo's current GitHub secrets)

The values below currently live in each repo's GitHub **secrets** (the `env:`
block of the old `deploy.yml`). They are **not secret** — move them into committed
stack files. Create `pulumi/Pulumi.dev.yaml` and `pulumi/Pulumi.prod.yaml` in each
repo. Same value in both is fine unless the processor genuinely differs by env.

> ⚠️ Keep `cron_*` / `schedule_enabled` per-env if you want prod schedules but
> not dev (common). Otherwise dev tasks fire on the prod cadence.

**idi-corporate-structure** (`pulumi/Pulumi.<stack>.yaml`) — old secret → key:

```yaml
config:
  idi:input_file: "<INPUT_FILE>"
  idi:cpu: "<ECS_TASK_CPU>"
  idi:memory: "<ECS_TASK_MEMORY>"
  idi:rate_limit: "<API_RATE_LIMIT>"
  idi:num_workers: "<NUM_WORKERS>"
  idi:input_sample_size: "<INPUT_SAMPLE_SIZE>"
  idi:openai_model: "<OPENAI_MODEL>"
  idi:cron_corporate_structure: "<CRON>"
```
SSM secrets (NOT here): `openai_api_key` (`OPENAI_API_KEY`), `sec_user_agent` (`SEC_USER_AGENT`).

**idi-company-info**:

```yaml
config:
  idi:geonames_user: "<GEONAMES_USER>"
  idi:cron_cik: "<CRON_CIK>"
  idi:cron_cusip: "<CRON_CUSIP>"
  idi:input_file_cik: "<INPUT_FILE_CIK>"
  idi:input_file_cusip: "<INPUT_FILE_CUSIP>"
  idi:output_dir: "<OUTPUT_DIR>"
  idi:failure_dir: "<FAILURE_DIR>"
  idi:cpu: "<ECS_TASK_CPU>"
  idi:memory: "<ECS_TASK_MEMORY>"
  idi:schedule_enabled: "<SCHEDULE_ENABLED>"
  idi:batch_size_cik: "<BATCH_SIZE_CIK>"
  idi:batch_size_cusip: "<BATCH_SIZE_CUSIP>"
  idi:buffer_size: "<BUFFER_SIZE>"
  idi:threshold_days: "<THRESHOLD_DAYS>"
  idi:match_score_threshold: "<MATCH_SCORE_THRESHOLD>"
```
SSM secret (NOT here): `permid_api_key` (`PERMID_API_KEY`).

**idi-sec-scraper**:

```yaml
config:
  idi:cpu: "<ECS_TASK_CPU>"
  idi:memory: "<ECS_TASK_MEMORY>"
  idi:rate_limit: "<API_RATE_LIMIT>"
  idi:max_workers: "<MAX_WORKERS>"
  idi:cron_sec_scraper: "<CRON>"
  idi:schedule_enabled: "<SCHEDULE_ENABLED>"
```
SSM secret (NOT here): `sec_user_agent` (`SEC_USER_AGENT`). It's marked
`--secret` today but is really the required SEC contact string — you may instead
commit it here if you'd rather audit it (decision noted in `.15`).

After moving each value, **delete the now-unused GitHub secret** so there's one
source of truth. The shared `_pulumi-deploy.yml` no longer sets any `idi:*` key
except `app_name`, so anything left only in a secret is silently dropped.

> All config values are strings in Pulumi stack files — quote numbers
> (`"1024"`) and booleans (`"true"`).

---

## `.17` — GitHub org / repo / environment configuration

Scope matters: **org** secrets/vars are a single value org-wide (not env-split);
only **environment**-scoped ones split dev/prod, and environments are per-repo.
So anything that genuinely differs dev↔prod must be environment-scoped.

### Org-level (genuinely one value for every repo)

Only values that are identical across all repos belong here.

```bash
gh variable set AWS_REGION          --org dsi-clinic --body "us-east-2"      --visibility all
gh variable set PULUMI_STATE_BUCKET --org dsi-clinic --body "<state-bucket>" --visibility all
# Optional ECR repo-name override (only if you don't want "<project>-<env>"):
# gh variable set ECR_REPOSITORY_PREFIX --org dsi-clinic --body "<prefix>" --visibility all
```

> **`PULUMI_CONFIG_PASSPHRASE` is NOT org-level.** It's the per-stack state
> encryption key, and each repo's state was initialized with its own passphrase —
> a single org value would only ever decrypt one repo's state and break the rest.
> Set it as a **repo-level secret** (below). An org secret is one shared value, so
> it cannot represent per-repo passphrases regardless of visibility scope. If a
> repo's passphrase also differs dev↔prod, scope it to the environment instead;
> it still resolves because the deploy/preview jobs set `environment:`.

> **Drop `PULUMI_ACCESS_TOKEN`** everywhere — vestigial with the `s3://` backend.

### Per repo — environments, env-scoped secrets, prod gate

Run for each of: `idi-ftm2j-shared`, `idi-corporate-structure`,
`idi-company-info`, `idi-sec-scraper`.

> **`dev`/`prod` are the AWS-deploy environments — additive, not a replacement
> for `release`.** The host repo already has a `release` environment that gates
> PyPI publishing (trusted-publisher OIDC; its name is referenced in PyPI's
> config — do not rename or delete it). After this, the host has three
> environments: `dev`, `prod`, `release`. The three docker siblings don't publish
> to PyPI, so they only get `dev`/`prod`.

```bash
REPO=dsi-clinic/idi-corporate-structure   # repeat per repo

# Create dev + prod environments (the host keeps its existing `release` too)
gh api -X PUT "repos/$REPO/environments/dev"
gh api -X PUT "repos/$REPO/environments/prod"

# Prod approval gate (required reviewers) — the reason we chose Environments.
# Replace <REVIEWER_USER_ID> (numeric) / or use a team id with type "Team".
gh api -X PUT "repos/$REPO/environments/prod" \
  -F "reviewers[][type]=User" -F "reviewers[][id]=<REVIEWER_USER_ID>"

# Env-scoped role ARNs (dev role != prod role)
gh secret set AWS_ROLE_ARN_DEPLOY --repo "$REPO" --env dev  --body "<dev-deploy-role-arn>"
gh secret set AWS_ROLE_ARN_DEPLOY --repo "$REPO" --env prod --body "<prod-deploy-role-arn>"
gh secret set AWS_ROLE_ARN_CHECKS --repo "$REPO" --env dev  --body "<dev-checks-role-arn>"
gh secret set AWS_ROLE_ARN_CHECKS --repo "$REPO" --env prod --body "<prod-checks-role-arn>"

# Repo-level: this repo's own Pulumi state passphrase (each repo differs),
# deploy key (SSH, push access to this repo only), prod-ready gate.
gh secret   set PULUMI_CONFIG_PASSPHRASE --repo "$REPO"    # this repo's passphrase
gh secret   set DEPLOY_KEY               --repo "$REPO"    # paste private key
gh variable set PROD_INFRA_READY         --repo "$REPO" --body "false"
```

> **Passphrase consolidation (fixes the pre-existing host bug):** the old host
> `checks.yml` read `PULUMI_SECRET_PASSPHRASE` while `deploy.yml` read
> `PULUMI_CONFIG_PASSPHRASE`. The shared workflows use **`PULUMI_CONFIG_PASSPHRASE`
> only**. For each repo, set that one secret to the passphrase that actually
> encrypted *that repo's* state, and delete any `PULUMI_SECRET_PASSPHRASE`. If the
> two ever held different values, preview and deploy were deriving different keys.

`GITHUB_TOKEN` is auto-provided (GHCR login) — nothing to set.

Why env-scoped (not `_DEV`/`_PROD` org secrets): only environment scope unlocks
the prod **approval gate**, and `secrets: inherit` does not forward environment
secrets unless the consuming job sets `environment:` — which the reusable
workflows do.

### `dev` branch protection (required before migrating a repo)

`_sync-dev.yml` **direct-pushes** to `dev` (no PR). If `dev` requires PRs/reviews,
that push is rejected and the release succeeds but dev sync fails. For each repo,
either relax `dev`'s protection or allow the `DEPLOY_KEY` to bypass:

```bash
# Inspect current protection
gh api "repos/$REPO/branches/dev/protection" 2>/dev/null || echo "no protection (push allowed)"
```

Deploy keys are not subject to "restrict who can push" the way users/apps are, but
they ARE blocked by "require a pull request before merging". Confirm a direct push
to `dev` by the deploy key is permitted before flipping the repo to the shared flow.

### Flip prod on when ready

Until a repo sets `PROD_INFRA_READY=true`, a `main` push still versions, releases,
and pushes the image to GHCR, but the prod `pulumi up` (and ECR sync) is skipped —
a safe transition default. When the prod stack is verified:

```bash
gh variable set PROD_INFRA_READY --repo "$REPO" --body "true"
```

---

## `.18`–`.20` — sibling caller files (for reference)

Each sibling's `deploy.yml` and `checks.yml` become thin callers pinned to an
**exact released version** of this repo, e.g. `@v0.1.9`. The shared workflows are
versioned like any other code: every change to `.github/**` cuts a new immutable
`vX.Y.Z` tag, and a consumer's behavior never changes until it bumps its pin.
Pick the latest `vX.Y.Z` tag of `idi-ftm2j-shared` (`gh release list -R
dsi-clinic/idi-ftm2j-shared`) and pin it; upgrade later by editing the pin.

Correct values (note `cov-package` is the **importable** package with the `idi_`
prefix — the old hyphenated `APP_NAME` measured zero coverage):

| Repo | `app-name` | `images` (name / dockerfile) | `cov-package` |
|---|---|---|---|
| idi-corporate-structure | `corporate-structure` | `orchestrator` / `dockerfiles/Dockerfile.orchestrator` | `idi_corporate_structure` |
| idi-company-info | `company-info` | `orchestrator` / `dockerfiles/Dockerfile.orchestrator` | `idi_company_info` |
| idi-sec-scraper | `sec-scraper` | `scraper` / `dockerfiles/Dockerfile.scraper` | `idi_sec_scraper` |

```yaml
# <sibling>/.github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main, dev]
    paths-ignore: ['**.md', 'docs/**']
  workflow_dispatch:
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false
permissions:           # caller must grant the union the nested jobs request
  contents: write      # version bump, tag/release, sync-dev
  id-token: write      # OIDC for AWS (pulumi deploy + ECR)
  packages: write      # push images to GHCR
jobs:
  pipeline:
    uses: dsi-clinic/idi-ftm2j-shared/.github/workflows/pipeline-docker.yml@v0.1.9  # pin an exact release
    secrets: inherit
    with:
      app-name: corporate-structure
      images: '[{"name":"orchestrator","dockerfile":"dockerfiles/Dockerfile.orchestrator"}]'
```

```yaml
# <sibling>/.github/workflows/checks.yml
name: Checks
on:
  pull_request:
    branches: [main, dev]
    paths-ignore: ['**.md', 'docs/**']
  workflow_dispatch:
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false
permissions:
  contents: read
  id-token: write          # OIDC for the pulumi preview role
  security-events: write   # CodeQL upload
jobs:
  checks:
    uses: dsi-clinic/idi-ftm2j-shared/.github/workflows/pipeline-checks.yml@v0.1.9  # pin an exact release
    secrets: inherit
    with:
      app-name: corporate-structure
      cov-package: idi_corporate_structure
```

Delete each sibling's old per-image jobs, the `[skip ci]` commit, the
`${GITHUB_ACTOR}` identity, and every `startsWith(github.ref, 'refs/heads/issue-')`
condition.

---

## Bootstrap & sequencing

1. Land the shared workflows on `main` → `_version.yml` cuts the first `vX.Y.Z`
   tag (a `.github/**` change now bumps the version without publishing to PyPI).
   That tag is what siblings pin. The host's own `deploy.yml`/`checks.yml` use
   local `./` refs and inlined `uv` setup, so they need **no** tag to run.
2. Set org vars/secrets (`.17`).
3. Per repo: create dev/prod environments, env-scoped role ARNs, `DEPLOY_KEY`,
   `PROD_INFRA_READY=false`; confirm `dev` allows the deploy-key push.
4. Wire config homes: StackReference (`.14`), SSM params + set values out-of-band
   (`.15`), committed `Pulumi.<stack>.yaml` (`.16`). Deploy the **shared** stack
   first per env so its exports exist for StackReference.
5. Migrate `idi-corporate-structure` first (simplest); verify a dev push, then a
   dev→main release + sync-dev; then roll company-info and sec-scraper.
6. Flip each repo's `PROD_INFRA_READY=true` once its prod stack is verified.
