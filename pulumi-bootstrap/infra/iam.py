"""IAM roles and policies for GitHub Actions OIDC authentication.

One pair of roles is provisioned **per repository** (see ``config.repos``) so each
repo's workflows assume only their own role ARNs — there is no shared org-wide role.
For every repo, two roles, one per trust boundary:

  1. checks  — assumed by `pulumi preview` on pull requests; read-only access.
  2. deploy  — assumed by `pulumi up` on this env's pushes; full deploy permissions.

Each trust policy pins the OIDC ``sub`` to ``repo:{org}/{repo}:...`` so only that
repository can assume the role, and (via ``config.*_sub_conditions``) to this
stack's own ``environment:{stack}`` so a dev token cannot assume the prod role and
vice-versa. The permission policy documents are identical across repos (scoped to
the shared ``idi-*`` resource namespace); isolation is at the trust boundary (who
can assume), expressed through distinct per-repo, per-env role ARNs.

Exposes ``checks_roles`` / ``deploy_roles``: dicts mapping repo name -> Role.
"""

import json

import pulumi
import pulumi_aws as aws

from infra import config, oidc


def _trust_policy(sub_conditions: list[str]) -> pulumi.Output:
    """Build a GitHub Actions OIDC assume-role trust policy.

    Uses ``StringLike`` so ``sub_conditions`` may mix exact repo prefixes with
    wildcard ref patterns (e.g. ``repo:dsi-rse/idi-sec-scraper:ref:refs/heads/*``).

    Args:
        sub_conditions: One or more ``token.actions.githubusercontent.com:sub``
            values to match against.
    """
    return oidc.oidc_provider.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Federated": arn},
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringEquals": {
                                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                            },
                            "StringLike": {
                                "token.actions.githubusercontent.com:sub": sub_conditions
                            },
                        },
                    }
                ],
            }
        )
    )


# -----------------------------------------------------------------------------
# Permission policy documents — identical across repos (scoped to the shared
# idi-* namespace). Built once and reused for every repo's role pair.
# -----------------------------------------------------------------------------

# Role 1: CHECKS (read-only, pull requests).
_CHECKS_POLICY_DOC = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PulumiStateRead",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    "arn:aws:s3:::idi-ftm2j-dev-pulumi-state",
                    "arn:aws:s3:::idi-ftm2j-dev-pulumi-state/*",
                ],
            },
            {
                "Sid": "EC2Read",
                "Effect": "Allow",
                "Action": [
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeRouteTables",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeVpcEndpoints",
                    "ec2:DescribeLaunchTemplates",
                    "ec2:DescribeLaunchTemplateVersions",
                    "ec2:DescribeImages",
                    "ec2:DescribeInstances",
                    "ec2:DescribeAvailabilityZones",
                    "ec2:DescribeVpcAttribute",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribePrefixLists",
                    "ec2:GetInstanceUefiData",
                ],
                "Resource": "*",
            },
            {
                "Sid": "IAMRead",
                "Effect": "Allow",
                "Action": [
                    "iam:GetRole",
                    "iam:GetRolePolicy",
                    "iam:GetInstanceProfile",
                    "iam:ListRolePolicies",
                    "iam:ListAttachedRolePolicies",
                    "iam:ListInstanceProfilesForRole",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ECRRead",
                "Effect": "Allow",
                "Action": [
                    "ecr:DescribeRepositories",
                    "ecr:ListTagsForResource",
                ],
                "Resource": "*",
            },
            {
                "Sid": "S3BucketRead",
                "Effect": "Allow",
                "Action": [
                    "s3:GetBucketLocation",
                    "s3:GetBucketTagging",
                    "s3:GetEncryptionConfiguration",
                    "s3:GetBucketPublicAccessBlock",
                    "s3:GetBucketOwnershipControls",
                    "s3:ListBucket",
                    "s3:GetBucketAcl",
                    "s3:GetBucketCors",
                    "s3:GetBucketLogging",
                    "s3:GetBucketObjectLockConfiguration",
                    "s3:GetLifecycleConfiguration",
                    "s3:GetBucketPolicy",
                    "s3:GetReplicationConfiguration",
                    "s3:GetBucketRequestPayment",
                    "s3:GetBucketVersioning",
                    "s3:GetBucketWebsite",
                    "s3:GetAccelerateConfiguration",
                    "s3:ListTagsForResource",
                ],
                "Resource": "arn:aws:s3:::idi-*",
            },
            {
                "Sid": "SQSRead",
                "Effect": "Allow",
                "Action": [
                    "sqs:GetQueueUrl",
                    "sqs:GetQueueAttributes",
                    "sqs:ListQueueTags",
                ],
                "Resource": "arn:aws:sqs:*:*:idi-*",
            },
            {
                "Sid": "SecretsManagerRead",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:ListSecretVersionIds",
                ],
                "Resource": "arn:aws:secretsmanager:*:*:secret:idi-*",
            },
            {
                "Sid": "AutoScalingRead",
                "Effect": "Allow",
                "Action": [
                    "autoscaling:DescribeAutoScalingGroups",
                    "autoscaling:DescribeTags",
                    "autoscaling:DescribeScalingActivities",
                ],
                "Resource": "*",
            },
            {
                # Read shared values + processor params during `pulumi preview`.
                "Sid": "SSMParameterRead",
                "Effect": "Allow",
                "Action": [
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                    "ssm:GetParametersByPath",
                    "ssm:ListTagsForResource",
                ],
                "Resource": "arn:aws:ssm:*:059007901663:parameter/idi/*",
            },
            {
                # DescribeParameters has no resource-level scoping (must be "*");
                # the AWS provider calls it to read parameter metadata.
                "Sid": "SSMDescribeParameters",
                "Effect": "Allow",
                "Action": "ssm:DescribeParameters",
                "Resource": "*",
            },
            {
                "Sid": "CloudWatchAlarmRead",
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:DescribeAlarms",
                    "cloudwatch:ListTagsForResource",
                ],
                "Resource": "arn:aws:cloudwatch:*:059007901663:alarm:idi-*",
            },
            {
                "Sid": "SNSRead",
                "Effect": "Allow",
                "Action": [
                    "sns:GetTopicAttributes",
                    "sns:GetSubscriptionAttributes",
                    "sns:ListSubscriptionsByTopic",
                    "sns:ListTagsForResource",
                ],
                "Resource": "arn:aws:sns:*:059007901663:idi-*",
            },
            {
                "Sid": "LogsMetricFilterRead",
                "Effect": "Allow",
                "Action": ["logs:DescribeMetricFilters"],
                "Resource": "arn:aws:logs:*:059007901663:log-group:/ecs/idi-*",
            },
            {
                "Sid": "STSCallerIdentity",
                "Effect": "Allow",
                "Action": "sts:GetCallerIdentity",
                "Resource": "*",
            },
        ],
    }
)

# Role 2: DEPLOY (full deploy, main/dev/release branches).
_DEPLOY_POLICY_DOC = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PulumiStateReadWrite",
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket",
                    "s3:DeleteObject",
                ],
                "Resource": [
                    "arn:aws:s3:::idi-ftm2j-dev-pulumi-state",
                    "arn:aws:s3:::idi-ftm2j-dev-pulumi-state/*",
                ],
            },
            {
                "Sid": "EC2SecurityGroup",
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateSecurityGroup",
                    "ec2:DeleteSecurityGroup",
                    "ec2:AuthorizeSecurityGroupIngress",
                    "ec2:AuthorizeSecurityGroupEgress",
                    "ec2:RevokeSecurityGroupIngress",
                    "ec2:RevokeSecurityGroupEgress",
                    "ec2:CreateTags",
                    "ec2:DeleteTags",
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeRouteTables",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeAvailabilityZones",
                    "ec2:DescribeVpcAttribute",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:CreateVpcEndpoint",
                    "ec2:DeleteVpcEndpoints",
                    "ec2:ModifyVpcEndpoint",
                    "ec2:DescribeVpcEndpoints",
                    "ec2:DescribePrefixLists",
                ],
                "Resource": "*",
            },
            {
                "Sid": "EC2VpcEndpoint",
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateVpcEndpoint",
                    "ec2:DeleteVpcEndpoints",
                    "ec2:ModifyVpcEndpoint",
                    "ec2:DescribeVpcEndpoints",
                    "ec2:DescribePrefixLists",
                ],
                "Resource": "*",
            },
            {
                "Sid": "IAMFull",
                "Effect": "Allow",
                "Action": [
                    "iam:CreateRole",
                    "iam:DeleteRole",
                    "iam:UpdateRole",
                    # Description-only edits go through the legacy
                    # UpdateRoleDescription API, not UpdateRole. Without this a
                    # processor `pulumi up` that changes a Role's description
                    # fails with AccessDenied even though the role can already be
                    # created/deleted/rewritten here.
                    "iam:UpdateRoleDescription",
                    "iam:PutRolePolicy",
                    "iam:DeleteRolePolicy",
                    "iam:AttachRolePolicy",
                    "iam:DetachRolePolicy",
                    "iam:TagRole",
                    "iam:UntagRole",
                    "iam:GetRole",
                    "iam:GetRolePolicy",
                    "iam:ListRolePolicies",
                    "iam:ListAttachedRolePolicies",
                ],
                "Resource": "arn:aws:iam::059007901663:role/idi-*",
            },
            {
                "Sid": "IAMPassRole",
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": "arn:aws:iam::059007901663:role/idi-*",
                "Condition": {
                    "StringEquals": {
                        "iam:PassedToService": [
                            "ec2.amazonaws.com",
                            "autoscaling.amazonaws.com",
                            "ecs-tasks.amazonaws.com",
                            "scheduler.amazonaws.com",
                        ]
                    }
                },
            },
            {
                "Sid": "IAMInstanceProfile",
                "Effect": "Allow",
                "Action": [
                    "iam:CreateInstanceProfile",
                    "iam:DeleteInstanceProfile",
                    "iam:AddRoleToInstanceProfile",
                    "iam:RemoveRoleFromInstanceProfile",
                    "iam:GetInstanceProfile",
                    "iam:ListInstanceProfilesForRole",
                    "iam:TagInstanceProfile",
                    "iam:UntagInstanceProfile",
                ],
                "Resource": "arn:aws:iam::*:instance-profile/idi-*",
            },
            {
                "Sid": "ECRAuth",
                "Effect": "Allow",
                "Action": "ecr:GetAuthorizationToken",
                "Resource": "*",
            },
            {
                "Sid": "ECRRepoFull",
                "Effect": "Allow",
                "Action": [
                    "ecr:CreateRepository",
                    "ecr:DeleteRepository",
                    "ecr:DescribeRepositories",
                    "ecr:TagResource",
                    "ecr:ListTagsForResource",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:PutImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutLifecyclePolicy",
                    "ecr:GetLifecyclePolicy",
                    "ecr:DeleteLifecyclePolicy",
                ],
                "Resource": "arn:aws:ecr:*:*:repository/idi-*",
            },
            {
                "Sid": "S3BucketFull",
                "Effect": "Allow",
                "Action": [
                    "s3:CreateBucket",
                    "s3:DeleteBucket",
                    "s3:PutBucketPublicAccessBlock",
                    "s3:PutBucketOwnershipControls",
                    "s3:PutEncryptionConfiguration",
                    "s3:PutBucketTagging",
                    "s3:GetBucketLocation",
                    "s3:GetBucketTagging",
                    "s3:GetEncryptionConfiguration",
                    "s3:GetBucketPublicAccessBlock",
                    "s3:GetBucketOwnershipControls",
                    "s3:ListBucket",
                    "s3:GetBucketAcl",
                    "s3:GetBucketCors",
                    "s3:GetBucketLogging",
                    "s3:GetBucketObjectLockConfiguration",
                    "s3:GetLifecycleConfiguration",
                    "s3:GetBucketPolicy",
                    "s3:GetReplicationConfiguration",
                    "s3:GetBucketRequestPayment",
                    "s3:GetBucketVersioning",
                    "s3:GetBucketWebsite",
                    "s3:GetAccelerateConfiguration",
                    "s3:ListTagsForResource",
                ],
                "Resource": "arn:aws:s3:::idi-*",
            },
            {
                "Sid": "SecretsManagerFull",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:CreateSecret",
                    "secretsmanager:DeleteSecret",
                    "secretsmanager:RestoreSecret",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:UpdateSecret",
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:TagResource",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:ListSecretVersionIds",
                    "secretsmanager:GetResourcePolicy",
                ],
                "Resource": "arn:aws:secretsmanager:*:*:secret:idi-*",
            },
            {
                "Sid": "KMSEncryption",
                "Effect": "Allow",
                "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
                "Resource": "arn:aws:kms:us-east-2:059007901663:key/4f8164b4-9db3-42a8-8b68-943491061efe",
            },
            {
                # Encrypt/decrypt SSM SecureString secret params (.15) with the
                # AWS-managed alias/aws/ssm key. Scoped via ViaService so the
                # role can only use KMS through SSM, not for anything else.
                "Sid": "KMSForSSMSecureString",
                "Effect": "Allow",
                "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
                "Resource": "*",
                "Condition": {"StringEquals": {"kms:ViaService": "ssm.us-east-2.amazonaws.com"}},
            },
            {
                # SSM Parameter Store: the shared stack writes /idi/<env>/shared/*
                # (bucket + DLQ names); processor stacks declare and read
                # /idi/<env>/<app>/* (incl. SecureString secrets). The deploy
                # policy is shared across every repo's role, so scoped to the
                # whole /idi/ tree.
                "Sid": "SSMParameterFull",
                "Effect": "Allow",
                "Action": [
                    "ssm:PutParameter",
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                    "ssm:GetParametersByPath",
                    "ssm:DeleteParameter",
                    "ssm:DeleteParameters",
                    "ssm:AddTagsToResource",
                    "ssm:RemoveTagsFromResource",
                    "ssm:ListTagsForResource",
                ],
                "Resource": "arn:aws:ssm:*:059007901663:parameter/idi/*",
            },
            {
                # The AWS provider reads parameter metadata via
                # DescribeParameters, a list action with no resource-level
                # scoping — it must be Resource "*".
                "Sid": "SSMDescribeParameters",
                "Effect": "Allow",
                "Action": "ssm:DescribeParameters",
                "Resource": "*",
            },
            {
                "Sid": "STSCallerIdentity",
                "Effect": "Allow",
                "Action": "sts:GetCallerIdentity",
                "Resource": "*",
            },
            {
                "Sid": "SQSFull",
                "Effect": "Allow",
                "Action": [
                    "sqs:CreateQueue",
                    "sqs:DeleteQueue",
                    "sqs:GetQueueAttributes",
                    "sqs:SetQueueAttributes",
                    "sqs:GetQueueUrl",
                    "sqs:TagQueue",
                    "sqs:UntagQueue",
                    "sqs:ListQueueTags",
                ],
                "Resource": "arn:aws:sqs:*:059007901663:idi-*",
            },
            {
                "Sid": "CloudWatchLogsFull",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:DeleteLogGroup",
                    "logs:PutRetentionPolicy",
                    "logs:DeleteRetentionPolicy",
                    "logs:TagLogGroup",
                    "logs:UntagLogGroup",
                    "logs:TagResource",
                    "logs:UntagResource",
                    "logs:ListTagsForResource",
                    "logs:ListTagsLogGroup",
                    # Processors alert on log-derived liveness metrics (e.g. CDT's
                    # poll-tick filter); filters live on the log group.
                    "logs:PutMetricFilter",
                    "logs:DeleteMetricFilter",
                    "logs:DescribeMetricFilters",
                ],
                "Resource": "arn:aws:logs:*:059007901663:log-group:/ecs/idi-*",
            },
            {
                "Sid": "CloudWatchLogsDescribe",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": "*",
            },
            {
                # Liveness alarms on the log-derived metrics above.
                "Sid": "CloudWatchAlarms",
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:PutMetricAlarm",
                    "cloudwatch:DeleteAlarms",
                    "cloudwatch:DescribeAlarms",
                    "cloudwatch:TagResource",
                    "cloudwatch:UntagResource",
                    "cloudwatch:ListTagsForResource",
                ],
                "Resource": "arn:aws:cloudwatch:*:059007901663:alarm:idi-*",
            },
            {
                # Alarm notification topics. Subscription ARNs extend the topic
                # ARN (idi-*:uuid), so one prefix covers both.
                "Sid": "SNSAlerts",
                "Effect": "Allow",
                "Action": [
                    "sns:CreateTopic",
                    "sns:DeleteTopic",
                    "sns:GetTopicAttributes",
                    "sns:SetTopicAttributes",
                    "sns:Subscribe",
                    "sns:Unsubscribe",
                    "sns:GetSubscriptionAttributes",
                    "sns:ListSubscriptionsByTopic",
                    "sns:TagResource",
                    "sns:UntagResource",
                    "sns:ListTagsForResource",
                ],
                "Resource": "arn:aws:sns:*:059007901663:idi-*",
            },
            {
                "Sid": "ECSFull",
                "Effect": "Allow",
                "Action": [
                    "ecs:CreateCluster",
                    "ecs:DeleteCluster",
                    "ecs:DescribeClusters",
                    "ecs:PutClusterCapacityProviders",
                    "ecs:RegisterTaskDefinition",
                    "ecs:DeregisterTaskDefinition",
                    "ecs:DescribeTaskDefinition",
                    "ecs:ListTaskDefinitions",
                    "ecs:CreateService",
                    "ecs:DeleteService",
                    "ecs:UpdateService",
                    "ecs:DescribeServices",
                    "ecs:TagResource",
                    "ecs:UntagResource",
                    "ecs:ListTagsForResource",
                ],
                "Resource": "*",
            },
            {
                "Sid": "EventBridgeSchedulerFull",
                "Effect": "Allow",
                "Action": [
                    "scheduler:CreateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                    "scheduler:UpdateSchedule",
                    "scheduler:ListSchedules",
                    "scheduler:TagResource",
                    "scheduler:UntagResource",
                    "scheduler:ListTagsForResource",
                ],
                "Resource": "arn:aws:scheduler:*:059007901663:schedule/default/idi-*",
            },
        ],
    }
)


# -----------------------------------------------------------------------------
# Per-repo role pairs — loop over config.repos, one checks + deploy role each.
# -----------------------------------------------------------------------------


def _repo_roles(repo: str) -> tuple[aws.iam.Role, aws.iam.Role]:
    """Create the checks + deploy OIDC role pair for a single repository.

    Both trust policies are scoped to ``repo:{org}/{repo}:...`` so only *repo*'s
    workflows can assume them; the permission documents are the shared
    ``_CHECKS_POLICY_DOC`` / ``_DEPLOY_POLICY_DOC``.

    Args:
        repo: Repository name within ``config.github_org`` (e.g. ``idi-sec-scraper``).

    Returns:
        ``(checks_role, deploy_role)`` for *repo*.
    """
    prefix = f"{repo}-{config.stack_name}-github"
    # Since the org rename GitHub embeds immutable IDs in the OIDC sub
    # (repo:org@id/repo@id:...), so match both the plain and ID-suffixed forms.
    # `@` is not a legal org/repo name character, so `@*` cannot be spoofed by
    # a look-alike name.
    repo_prefixes = [
        f"repo:{config.github_org}/{repo}",
        f"repo:{config.github_org}@*/{repo}@*",
    ]

    checks_role = aws.iam.Role(
        f"idi-role-github-checks-{repo}",
        name=f"{prefix}-checks",
        description=f"Read-only access for pulumi preview on {repo} pull requests",
        assume_role_policy=_trust_policy(
            [f"{p}:{sub}" for p in repo_prefixes for sub in config.checks_sub_conditions]
        ),
        tags=config.tags({"Name": f"{prefix}-checks", "repo": repo}),
    )
    aws.iam.RolePolicy(
        f"idi-policy-github-checks-{repo}",
        role=checks_role.id,
        policy=_CHECKS_POLICY_DOC,
    )

    deploy_role = aws.iam.Role(
        f"idi-role-github-deploy-{repo}",
        name=f"{prefix}-deploy",
        description=f"Full deploy access for pulumi up on {repo} ({config.stack_name})",
        assume_role_policy=_trust_policy(
            [f"{p}:{sub}" for p in repo_prefixes for sub in config.deploy_sub_conditions]
        ),
        tags=config.tags({"Name": f"{prefix}-deploy", "repo": repo}),
    )
    aws.iam.RolePolicy(
        f"idi-policy-github-deploy-{repo}",
        role=deploy_role.id,
        policy=_DEPLOY_POLICY_DOC,
    )

    return checks_role, deploy_role


# repo name -> Role, for every repo in config.repos.
checks_roles: dict[str, aws.iam.Role] = {}
deploy_roles: dict[str, aws.iam.Role] = {}
for _repo in config.repos:
    _checks, _deploy = _repo_roles(_repo)
    checks_roles[_repo] = _checks
    deploy_roles[_repo] = _deploy
