# GitHub Actions deploys with short-lived credentials from OIDC: the workflow asks
# GitHub for a signed token, and AWS STS swaps it for a role session that expires
# in an hour. No long-lived AWS keys are stored in GitHub.

locals {
  github_oidc_url = "token.actions.githubusercontent.com"
  # Same ARN whether this stack creates the provider or it already exists.
  github_oidc_provider_arn = "arn:aws:iam::${local.account_id}:oidc-provider/${local.github_oidc_url}"
}

# An account can only have one provider per URL; set create_github_oidc_provider
# to false if another stack already created it.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://${local.github_oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
  # IAM validates GitHub's certificate against its own trusted CAs; the thumbprints
  # are only required by the API and are GitHub's published values.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only workflows running on the main branch of this one repository. (Pull
    # requests, other branches, forks and GitHub "environments" get a different
    # subject and are refused.)
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_sub_prefix}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${local.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_assume.json
  max_session_duration = 3600

  depends_on = [aws_iam_openid_connect_provider.github]
}

locals {
  ecs_arn_prefix = "arn:aws:ecs:${var.aws_region}:${local.account_id}"
}

# Least privilege for exactly what deploy.yml does.
data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PushAppImage"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages", # "does this commit's image already exist?"
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  # These two actions don't support resource-level permissions.
  statement {
    sid       = "TaskDefinitions"
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  statement {
    sid     = "UpdateServices"
    actions = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = [
      for svc in [aws_ecs_service.api, aws_ecs_service.worker, aws_ecs_service.beat] :
      "${local.ecs_arn_prefix}:service/${aws_ecs_cluster.this.name}/${svc.name}"
    ]
  }

  # One-off tasks (migrations), only from our task families and only in our cluster.
  statement {
    sid     = "RunOneOffTasks"
    actions = ["ecs:RunTask"]
    resources = [
      for td in aws_ecs_task_definition.this : "${local.ecs_arn_prefix}:task-definition/${td.family}:*"
    ]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }

  statement {
    sid       = "WatchTasks"
    actions   = ["ecs:DescribeTasks"]
    resources = ["${local.ecs_arn_prefix}:task/${aws_ecs_cluster.this.name}/*"]
  }

  # Registering/running a task definition hands its roles to ECS; allow exactly
  # our two roles, and only to ECS.
  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task_execution.arn, aws_iam_role.task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # Print the migration task's output in the job log when it fails.
  statement {
    sid       = "ReadMigrationLogs"
    actions   = ["logs:GetLogEvents"]
    resources = ["${aws_cloudwatch_log_group.service["worker"].arn}:*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy-adaptiveroute"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}

locals {
  # Newer repositories get "immutable" OIDC subjects that embed numeric IDs, e.g.
  # repo:OWNER@123/REPO@456 (see sub_claim_prefix in
  # `gh api repos/OWNER/REPO/actions/oidc/customization/sub`). Pinning the IDs means a
  # re-created account or repo with the same name cannot assume this role.
  github_sub_prefix = var.github_oidc_sub_prefix != "" ? var.github_oidc_sub_prefix : "repo:${var.github_repo}"
}
