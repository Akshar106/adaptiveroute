# Two roles per task, with different jobs:
#   execution role - used by the ECS agent BEFORE the app starts: pull the image,
#                    fetch the secrets, create log streams.
#   task role      - used by the code INSIDE the containers: here only the ADOT
#                    collector needs AWS access (X-Ray and AMP).
# The app itself makes no AWS API calls.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    # Only ECS acting for this account may assume the role ("confused deputy" guard).
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- execution role ----------------------------------------------------------------

resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task_execution" {
  # GetAuthorizationToken has no resource-level permissions; it only returns a
  # registry login, the actions below decide which repository can be read.
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PullAppImage"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid       = "WriteContainerLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for lg in aws_cloudwatch_log_group.service : "${lg.arn}:*"]
  }

  # Exactly the application's secrets, nothing else in the account. They use the
  # AWS-managed KMS key, so no kms:Decrypt grant is needed.
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.app_secret_arns
  }
}

resource "aws_iam_role_policy" "task_execution" {
  name   = "pull-image-read-secrets-write-logs"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution.json
}

# --- task role ---------------------------------------------------------------------

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task" {
  # X-Ray write APIs do not support resource-level permissions, hence "*".
  statement {
    sid       = "XRayWrite"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }

  statement {
    sid       = "AmpRemoteWrite"
    actions   = ["aps:RemoteWrite"]
    resources = [aws_prometheus_workspace.this.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "xray-and-amp-write"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}
