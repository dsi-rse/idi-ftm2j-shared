"""Pulumi configuration and shared constants.

All other modules import from here rather than reading Pulumi config directly,
so config keys are defined and validated in a single place.

Module-level values
-------------------
project_name         : Pulumi project name (``idi-bootstrap``).
stack_name           : Active stack (e.g. ``dev``).
app_name             : Application identifier from ``idi:app_name`` config; defaults
                       to ``ftm2j-shared``.
name_prefix          : ``{project}-{stack}-{app_name}`` — prepended to every resource
                       name to keep them unique and identifiable.
github_org           : GitHub organisation whose repos may assume the OIDC roles.
repos                : Repositories that each get their own isolated pair of OIDC
                       roles (checks + deploy). Add a repo here and re-deploy to
                       provision its role pair. Overridable via ``idi:repos``.
checks_sub_conditions: Sub patterns for the checks role's trust policy, derived
                       per stack (``environment:{stack}`` + PR marker).
deploy_sub_conditions: Sub patterns for the deploy role's trust policy, derived
                       per stack: dev -> dev/issue refs + ``environment:dev``;
                       prod -> main/release refs + ``environment:prod``. Each is
                       appended to the per-repo prefix in iam.py. Scoping each
                       stack to its own ``environment:`` is what keeps a dev token
                       from assuming the prod role (and vice-versa).
aws_region           : Deployment region from ``aws:region`` config.
caller               : AWS caller identity (exposes ``.account_id``, ``.arn``).
"""

import pulumi
import pulumi_aws as aws

config = pulumi.Config("idi")
project_name = pulumi.get_project()
stack_name = pulumi.get_stack()
app_name = config.get("app_name") or "ftm2j-shared"
name_prefix = f"{project_name}-{stack_name}-{app_name}"

github_org = config.require("github_org")

# Each repo gets its own isolated checks + deploy role pair (see iam.py). Add a
# repo here (or override via `idi:repos`) and re-deploy this stack to provision
# its roles; the repo's workflows then assume only its own role ARNs.
repos: list[str] = config.get_object("repos") or [
    "idi-ftm2j-shared",
    "idi-corporate-structure",
    "idi-company-info",
    "idi-sec-scraper",
    "idi-company-facts",
    "commercial-debt-tracker",
    "idi-company-facts",
]

# Trust-policy sub conditions, derived PER STACK so each env's roles trust only
# their own environment — a dev token cannot assume the prod role, or vice-versa.
# Every job that assumes these roles sets `environment:`, so the OIDC `sub` is
# always `repo:{org}/{repo}:environment:{stack}`; that env sub is the entry that
# actually matches. The env-appropriate branch refs are kept for clarity/defense
# and are disjoint across stacks (dev/issue vs main/release), so they add no
# cross-env trust. Overridable per stack via idi:checks/deploy_sub_conditions.
if stack_name == "prod":
    _default_checks = ["pull_request", "environment:prod"]
    _default_deploy = ["ref:refs/heads/main", "ref:refs/heads/release/*", "environment:prod"]
else:
    _default_checks = ["pull_request", "environment:dev"]
    _default_deploy = ["ref:refs/heads/dev", "ref:refs/heads/issue-*", "environment:dev"]

checks_sub_conditions: list[str] = config.get_object("checks_sub_conditions") or _default_checks
deploy_sub_conditions: list[str] = config.get_object("deploy_sub_conditions") or _default_deploy

aws_config = pulumi.Config("aws")
aws_region = aws_config.require("region")
caller = aws.get_caller_identity()


def tags(extra: dict | None = None) -> dict:
    """Return the standard tag dict, optionally merged with resource-specific tags.

    Args:
        extra: Additional tags to include (e.g. ``{"Name": "my-resource"}``).
               Keys in *extra* override the standard tags if they clash.
    """
    t = {
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
        "app_name": app_name,
    }
    if extra:
        t.update(extra)
    return t
