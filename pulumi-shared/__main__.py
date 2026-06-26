"""Pulumi entrypoint for the IDI FTM2J shared infrastructure stack.

Shared resources used by all FTM2J processor pipelines (corporate-structure,
company-info, etc.). Each processor's Pulumi stack references these outputs
rather than creating its own copies.

Stack outputs:
    processor_bucket_name  — S3 bucket ID for pipeline input/output/failures.
    processor_bucket_arn   — S3 bucket ARN for IAM policy construction.
    s3_endpoint_id         — VPC gateway endpoint ID for the S3 service.
    s3_endpoint_arn        — VPC gateway endpoint ARN.
    dlq_url                — SQS dead-letter queue URL for EventBridge Scheduler.
    dlq_arn                — SQS dead-letter queue ARN for IAM policy construction.
"""

# Import order matters: config first, then resources by dependency
import pulumi
from infra import network, params, queue, storage

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------

# Storage
pulumi.export("processor_bucket_name", storage.processor_bucket.id)
pulumi.export("processor_bucket_arn", storage.processor_bucket.arn)

# Networking
pulumi.export("s3_endpoint_id", network.s3_endpoint.id)
pulumi.export("s3_endpoint_arn", network.s3_endpoint.arn)

# Queue
pulumi.export("dlq_url", queue.dlq.url)
pulumi.export("dlq_arn", queue.dlq.arn)

# SSM cross-stack parameters (names processors read from)
pulumi.export("ssm_processor_bucket_name_param", params.processor_bucket_name_param.name)
pulumi.export("ssm_dlq_name_param", params.dlq_name_param.name)
