"""Entry point for the idi-bootstrap Pulumi stack.

Provisions the account-level GitHub Actions OIDC provider plus a per-repository
pair of IAM roles (one set per entry in ``config.repos``), then exports their
identifiers so each repo can wire its own role ARNs without hard-coding them.

Stack outputs
-------------
oidc_provider_arn   : ARN of the GitHub Actions OIDC identity provider.
oidc_provider_name  : Issuer URL of the OIDC provider.
checks_role_arns    : {repo -> ARN} of the read-only role assumed on that repo's
                      pull requests and manual workflow runs.
checks_role_names   : {repo -> name} of the checks roles.
deploy_role_arns    : {repo -> ARN} of the full-deploy role assumed on that repo's
                      main/dev/release branch pushes.
deploy_role_names   : {repo -> name} of the deploy roles.
"""

import pulumi
from infra import iam, oidc

pulumi.export("oidc_provider_arn", oidc.oidc_provider.arn)
pulumi.export("oidc_provider_name", oidc.oidc_provider.id)

pulumi.export("checks_role_arns", {repo: role.arn for repo, role in iam.checks_roles.items()})
pulumi.export("checks_role_names", {repo: role.name for repo, role in iam.checks_roles.items()})
pulumi.export("deploy_role_arns", {repo: role.arn for repo, role in iam.deploy_roles.items()})
pulumi.export("deploy_role_names", {repo: role.name for repo, role in iam.deploy_roles.items()})
