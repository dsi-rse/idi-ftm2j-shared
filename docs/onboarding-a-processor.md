# Onboarding a processor onto the shared CI/CD flow

Copy-down checklist for putting a **processor** repo (e.g. `idi-corporate-structure`,
`idi-company-info`, `idi-sec-scraper`) onto the shared workflows
[`pipeline-docker.yml`](../.github/workflows/pipeline-docker.yml) +
[`pipeline-checks.yml`](../.github/workflows/pipeline-checks.yml).

Scope: **processors only.** This repo's own PyPI flow (`deploy.yml`, which is
self-contained and not shared) is out of scope. Org is assumed to be `dsi-rse`; adjust `gh`
commands to your auth/admin scope.

Work top to bottom. A repo is fully onboarded when a `dev` push releases + pushes
to GHCR + deploys the dev stack, a `dev`→`main` PR releases a stable version and
auto-syncs back to `dev`, and prod is flipped on once its AWS infra exists.

---

## 1. Workflow callers

Both files are thin callers pinned to an **exact released tag** of
`idi-ftm2j-shared` — never `@main` or a branch. The shared workflows are versioned
like code: every `.github/**` change cuts a new immutable `vX.Y.Z`, and your repo's
behavior never changes until you bump the pin.

`.github/workflows/deploy.yml` (post-merge: version, build/push image, deploy):

```yaml
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
    uses: dsi-rse/idi-ftm2j-shared/.github/workflows/pipeline-docker.yml@vX.Y.Z  # pin an exact release
    secrets: inherit
    with:
      app-name: <app-name>  # e.g. corporate-structure
      images: '[{"name":"orchestrator","dockerfile":"dockerfiles/Dockerfile.orchestrator"}]'
```

`.github/workflows/checks.yml` (PR gate: lint, test, security, pulumi preview):

```yaml
name: Checks
on:
  pull_request:
    branches: [main, dev]
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
    uses: dsi-rse/idi-ftm2j-shared/.github/workflows/pipeline-checks.yml@vX.Y.Z  # pin an exact release
    secrets: inherit
    with:
      app-name: <app-name>  # e.g. corporate-structure
      cov-package: <cov-package>  # e.g. idi_corporate_structure
```

`secrets: inherit` forwards every Github repo/org/environment secret; do **not** list
secrets individually (env-scoped secrets like the role ARNs can't be re-declared as
`workflow_call` secrets — they resolve because the nested jobs set `environment:`).

`cov-package` is the **importable** package (underscore form, `idi_` prefix), not
the hyphenated `app-name` — a hyphenated value measures zero coverage. Pass
multiple space-separated. Per-repo values:

| Repo | `app-name` | `images` (name / dockerfile) | `cov-package` |
|---|---|---|---|
| idi-corporate-structure | `corporate-structure` | `orchestrator` / `dockerfiles/Dockerfile.orchestrator` | `idi_corporate_structure` |
| idi-company-info | `company-info` | `orchestrator` / `dockerfiles/Dockerfile.orchestrator` | `idi_company_info` |
| idi-sec-scraper | `sec-scraper` | `orchestrator` / `dockerfiles/Dockerfile.scraper` | `idi_sec_scraper` |

---

## 2. Repo settings / branch protection

Default branch is **`dev`**. Two rulesets, `dev` and `main`, with **different**
allowed merge methods:

| Branch | Allowed merge method | Why |
|---|---|---|
| `dev` | **Squash only** (disable merge commits + rebase) | Linear history; the post-merge pipeline keys off the single squash commit per merged PR. |
| `main` | **Merge commit only** (disable squash + rebase) | The `dev`→`main` PR preserves history so release notes capture every change, and the `sync-dev` merge-back stays clean. |

Required on **both** rulesets (PRs into `dev` and into `main`):

- ✅ Require a pull request before merging
- ✅ Require status checks to pass: **Lint, Test, Security, Pulumi Preview** (the four jobs from `pipeline-checks`)
- ✅ Require code scanning results — CodeQL, "High or higher"
- ✅ Restrict deletions
- ✅ Block force pushes
- Deploy key on the **bypass** list, set to "Always allow" (see §3) — **required on `dev`**: the `sync-dev` job direct-pushes the `main`→`dev` merge-back with no PR, and branch protection's "require a pull request before merging" otherwise rejects it. (Deploy keys are not subject to "restrict who can push", but they *are* blocked by the PR requirement.)

Also enable **Settings → General → Automatically delete head branches** so merged
issue branches are cleaned up instead of accumulating in the repo. This only
deletes the head branch of a merged PR; `dev` and `main` are unaffected, and
"Restrict deletions" on those rulesets still applies.

---

## 3. Deploy key setup

The `DEPLOY_KEY` is what lets the pipeline push past branch protection for the
version-bump commit (`main`), the tag + GitHub Release, and the `main`→`dev` sync
push. The commits are authored as `idi-deploy-bot`, and the pipeline's
committer-identity guard skips bot-authored head commits — that
is what breaks the deploy→commit→deploy loop.

```bash
# 1. Generate an ed25519 keypair (no passphrase)
ssh-keygen -t ed25519 -C "deploy key for <repo>" -f ~/.ssh/<repo>_deploy_key -N ""

# 2. Add the PUBLIC key as a repo Deploy Key WITH WRITE ACCESS
#    (repo Settings → Deploy keys → Add deploy key → check "Allow write access")

# 3. Store the PRIVATE key as the DEPLOY_KEY repo secret
gh secret set DEPLOY_KEY --repo "dsi-rse/<repo>" < ~/.ssh/<repo>_deploy_key
```

Then add the deploy key to `dev` branch ruleset's branch-protection bypass list in the repository settings: `Add bypass` > `Deploy keys` (§2).

Add the deploy key to the Bitwarden account, following the naming convention used by other `DEPLOY_KEY` variables.

---

## 4. Provision the repo's OIDC roles (pulumi-bootstrap)

Each repo assumes its **own** AWS roles — there is no shared org-wide role. The
`pulumi-bootstrap` stack loops over a repo list and creates a `checks` + `deploy`
role pair per repo, trust-scoped to `repo:dsi-rse/<repo>` so only that repo's
workflows can assume them.

1. Add the repo to the `repos` list in
   [`pulumi-bootstrap/infra/config.py`](../pulumi-bootstrap/infra/config.py) (or
   override `idi:repos` in `Pulumi.<stack>.yaml`):
   ```python
   repos: list[str] = config.get_object("repos") or [
       "idi-ftm2j-shared",
       "idi-corporate-structure",
       "idi-company-info",
       "idi-sec-scraper",
       "idi-<your-new-repo>",   # add here
   ]
   ```
2. Re-deploy the bootstrap stack **per env** (run locally — it mints the very roles
   CI uses, so CI can't deploy it). `pulumi up` against `dev` and `prod`.
   ```
   cd pulumi-bootstrap
   pulumi login s3://{PULUMI_STATE_BUCKET}/ftm2j-shared/bootstrap
   export PULUMI_CONFIG_PASSPHRASE= # check Bitwarden entry PULUMI_CONFIG_PASSPHRASE_BOOTSTRAP
   pulumi stack select {dev|prod}
   pulumi up
   ```
3. Read the new ARNs from the stack outputs — `checks_role_arns` and
   `deploy_role_arns` are maps keyed by repo:
   ```bash
   cd pulumi-bootstrap && pulumi stack output deploy_role_arns
   ```

   Use that repo's `checks` ARN for `AWS_ROLE_ARN_CHECKS` and its `deploy` ARN for
   `AWS_ROLE_ARN_DEPLOY` in §5.
  
Add these values to Bitwarden.

---

## 5. Where each value goes (routing table)

Every configuration value has exactly one home. 

For secret values (Github secrets or app secrets stored in SSM), add an entry to Bitwarden, following the naming convention used by other secrets.

| Value kind | Examples | Home | How it's set |
|---|---|---|---|
| **Non-secret per-processor knobs** | `cpu`, `memory`, `cron_*`, `schedule_enabled`, model, sample size, `input_file`, batch sizes | Committed `pulumi/Pulumi.dev.yaml` + `pulumi/Pulumi.prod.yaml` (`idi:*` keys) | Commit to git. All values are **strings** — quote numbers (`"1024"`) and booleans (`"true"`). Keep `cron_*`/`schedule_enabled` per-env if prod should run on a different cadence than dev. |
| **Genuine secrets** | API keys (`openai_api_key`, `permid_api_key`, …) | AWS SSM **`SecureString`** at `/idi/<env>/<app>/secrets/*` | Pulumi creates a placeholder; set the real value after the parameter is created by the first deploy: `aws ssm put-parameter --name /idi/<env>/<app>/secrets/<key> --type SecureString --value '<v>' --overwrite`. **Repos are PUBLIC — never commit these, even encrypted. Secrets should also be stored in Bitwarden.** |
| **Shared values** | processor bucket name, DLQ name | AWS SSM **`String`** at `/idi/<env>/shared/*` | **Nothing per repo.** Published by the shared stack; processors read via `aws.ssm.get_parameter`. |
| **GitHub Environment secrets** (dev/prod) | `AWS_ROLE_ARN_DEPLOY`, `AWS_ROLE_ARN_CHECKS` | Per-repo, scoped to the `dev` and `prod` environments | `gh secret set <NAME> --repo "$REPO" --env dev` / `--env prod`. Values are this repo's own bootstrap role ARNs from §4. Env scope is required so the prod approval gate and per-env role ARNs work. |
| **Pulumi state passphrase** | `PULUMI_CONFIG_PASSPHRASE` | Per-repo secret (env-scoped only if it differs dev↔prod) | `gh secret set PULUMI_CONFIG_PASSPHRASE --repo "$REPO"`. **Not org-level** — each repo's state was encrypted with its own passphrase; one org value would decrypt only one repo. |
| **GitHub vars** | `PULUMI_STATE_BUCKET`, `PROD_INFRA_READY` | Org-level for the values identical across repos; `PROD_INFRA_READY` is per-repo | `gh variable set …`. See §6 for `PROD_INFRA_READY`. |

`idi:app_name` is **not** committed to the stack files — the workflow sets it from
the `app-name` caller input.

Github environments: processors get **`dev`** and **`prod`** only. Each processor should have the following secrets and variables in Github:

- **Secrets**:
  - **Environment**: `AWS_ROLE_ARN_CHECKS`, `AWS_ROLE_ARN_DEPLOY`
  - **Repository**: `DEPLOY_KEY`, `PULUMI_CONFIG_PASSPHRASE`
- **Variables**:
  - **Environment**: `PULUMI_STATE_BUCKET`
  - **Repository**: `PROD_INFRA_READY`
  - **Organization**: `AWS_REGION`

Anything other configurations should either be the committed `pulumi/Pulumi.{env}.yaml` files (if non-secret) or stored in SSM and managed by Pulumi (if secret).


---

## 6. Prod-readiness sequence

Migration is staged so a repo can go live on `dev` immediately while prod stays
dark until its AWS infra exists.

1. Set `PROD_INFRA_READY=false` (the safe default) when onboarding:
   ```bash
   gh variable set PROD_INFRA_READY --repo "dsi-rse/<repo>" --body "false"
   ```
   A `main` push still **versions, releases, and pushes the image to GHCR**, but
   `deploy-pulumi` (and the ECR sync) is **skipped**. Dev is unaffected — it always
   deploys.
2. Deploy the **shared** stack first per env so its `/idi/<env>/shared/*` SSM params
   exist for the processor to read.
3. Verify on `dev`: push to `dev`, confirm release + GHCR push + dev stack deploy;
   then do a `dev`→`main` PR and confirm the stable release and `sync-dev` merge-back.
4. Once the prod AWS stack is provisioned and verified, flip the gate:
   ```bash
   gh variable set PROD_INFRA_READY --repo "dsi-rse/<repo>" --body "true"
   ```
   From then on `main` pushes also sync to ECR and run `deploy-pulumi` against prod
   (still behind the `prod` environment's approval gate).
