"""SSM Parameter Store entries that publish this stack's shared outputs.

Processor stacks read these (via `aws.ssm.get_parameter`) to discover the
shared S3 bucket and EventBridge Scheduler dead-letter queue, instead of a
Pulumi StackReference. All Pulumi projects in this org share the name `idi`,
which makes cross-stack StackReference ambiguous on the self-managed S3 backend
(and the per-repo state backends aren't mutually readable), so SSM is the
cross-stack value bus: one writer (this stack), many readers (the processors).

Non-secret values, so plain `String` parameters under `/idi/<stack>/shared/*`.
Genuine secrets live elsewhere as `SecureString` under
`/idi/<stack>/<app>/secrets/*`.
"""

import pulumi_aws as aws

from . import config, queue, storage

_prefix = f"/idi/{config.stack_name}/shared"

# S3 processor bucket name — read by every processor for input/output/failures.
processor_bucket_name_param = aws.ssm.Parameter(
    "idi-ssm-shared-processor-bucket-name",
    name=f"{_prefix}/processor_bucket_name",
    type="String",
    value=storage.processor_bucket.id,
    description="Shared processor S3 bucket name (published for processor stacks).",
    tags=config.tags(),
)

# Scheduler dead-letter queue name — processors look it up to target their
# EventBridge schedules' DLQ.
dlq_name_param = aws.ssm.Parameter(
    "idi-ssm-shared-dlq-name",
    name=f"{_prefix}/dlq_name",
    type="String",
    value=queue.dlq.name,
    description="Shared EventBridge Scheduler DLQ name (published for processor stacks).",
    tags=config.tags(),
)
