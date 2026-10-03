# Security groups are the firewall between the tiers:
#
#   internet --80/443--> alb --8000--> api --5432--> rds
#                                      api --6379--> redis
#                                   worker --5432/6379--> rds / redis   (worker + beat + one-off tasks)
#
# Rules are separate resources (not inline blocks) so groups can reference each
# other without dependency cycles. A Terraform-managed group starts with no rules
# at all, so everything not listed here is denied.
#
# Not listed because security groups don't filter it: DNS to the VPC resolver,
# and traffic between containers of the same task (they share localhost, which is
# how the ADOT sidecar receives traces and scrapes metrics).

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public load balancer"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "API tasks: reachable only from the ALB"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group" "worker" {
  name        = "${local.name}-worker"
  description = "Celery worker, beat and one-off tasks (migrations): no inbound"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "PostgreSQL: reachable only from api and worker tasks"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "Redis: reachable only from api and worker tasks"
  vpc_id      = module.vpc.vpc_id
}

# --- ALB ---------------------------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from the internet (redirects to HTTPS when a certificate is set)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count = local.https_enabled ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from the internet"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward requests and health checks to API tasks"
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

# --- API ---------------------------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  description                  = "HTTP from the ALB only"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

# --- outbound rules shared by api and worker tasks ---------------------------------

locals {
  task_security_groups = {
    api    = aws_security_group.api.id
    worker = aws_security_group.worker.id
  }
}

# HTTPS to the internet via the NAT gateway: Groq API, ECR, Secrets Manager,
# CloudWatch Logs, X-Ray, AMP. Port 443 only.
resource "aws_vpc_security_group_egress_rule" "task_https" {
  for_each = local.task_security_groups

  security_group_id = each.value
  description       = "HTTPS to AWS APIs and the LLM provider"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "task_to_rds" {
  for_each = local.task_security_groups

  security_group_id            = each.value
  description                  = "PostgreSQL"
  referenced_security_group_id = aws_security_group.rds.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "task_to_redis" {
  for_each = local.task_security_groups

  security_group_id            = each.value
  description                  = "Redis (TLS)"
  referenced_security_group_id = aws_security_group.redis.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}

# --- data stores -------------------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "rds_from_tasks" {
  for_each = local.task_security_groups

  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL from ${each.key} tasks"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_tasks" {
  for_each = local.task_security_groups

  security_group_id            = aws_security_group.redis.id
  description                  = "Redis from ${each.key} tasks"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}
