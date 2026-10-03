output "public_url" {
  description = "Base URL of the API (the deploy smoke test calls <this>/readyz)."
  value       = local.public_url
}

output "alb_dns_name" {
  description = "Point your domain's CNAME/alias record here when using HTTPS."
  value       = aws_lb.this.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service_names" {
  value = {
    api    = aws_ecs_service.api.name
    worker = aws_ecs_service.worker.name
    beat   = aws_ecs_service.beat.name
  }
}

output "task_definition_families" {
  value = { for k, td in aws_ecs_task_definition.this : k => td.family }
}

output "private_subnet_ids" {
  description = "Subnets for one-off tasks (migrations, create-api-key)."
  value       = module.vpc.private_subnets
}

output "task_security_group_id" {
  description = "Security group for one-off tasks (the worker group: reaches Postgres and Redis, no inbound)."
  value       = aws_security_group.worker.id
}

output "deploy_role_arn" {
  description = "IAM role the GitHub deploy workflow assumes via OIDC."
  value       = aws_iam_role.github_deploy.arn
}

output "amp_endpoint" {
  description = "Amazon Managed Prometheus workspace endpoint (Grafana data source URL)."
  value       = aws_prometheus_workspace.this.prometheus_endpoint
}

output "amp_remote_write_url" {
  value = local.amp_remote_write_url
}

output "log_groups" {
  value = { for k, lg in aws_cloudwatch_log_group.service : k => lg.name }
}

# Everything deploy.yml reads from repository variables, ready for `gh variable set`
# (see README.md).
output "github_variables" {
  value = {
    AWS_REGION             = var.aws_region
    AWS_DEPLOY_ROLE_ARN    = aws_iam_role.github_deploy.arn
    ECR_REPOSITORY         = aws_ecr_repository.app.name
    ECS_CLUSTER            = aws_ecs_cluster.this.name
    ECS_SERVICE_API        = aws_ecs_service.api.name
    ECS_SERVICE_WORKER     = aws_ecs_service.worker.name
    ECS_SERVICE_BEAT       = aws_ecs_service.beat.name
    ECS_TASK_FAMILY_API    = aws_ecs_task_definition.this["api"].family
    ECS_TASK_FAMILY_WORKER = aws_ecs_task_definition.this["worker"].family
    ECS_TASK_FAMILY_BEAT   = aws_ecs_task_definition.this["beat"].family
    PRIVATE_SUBNETS        = join(",", module.vpc.private_subnets)
    TASK_SECURITY_GROUP    = aws_security_group.worker.id
    PUBLIC_URL             = local.public_url
  }
}
