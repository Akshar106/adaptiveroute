# Public Application Load Balancer in front of the API tasks.
resource "aws_lb" "this" {
  name               = local.name
  load_balancer_type = "application"
  internal           = false
  subnets            = module.vpc.public_subnets
  security_groups    = [aws_security_group.alb.id]

  # Synchronous queries may run up to 90 s (AR sync_request_timeout_s), and the
  # default 60 s idle timeout would cut them off with a 504.
  idle_timeout = 120

  # Reject requests with malformed header names instead of passing them on.
  drop_invalid_header_fields = true

  enable_deletion_protection = var.deletion_protection
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
  protocol    = "HTTP" # TLS ends at the ALB; ALB-to-task traffic stays in the VPC.
  target_type = "ip"   # Fargate tasks register by their ENI's private IP.
  vpc_id      = module.vpc.vpc_id

  # On deploys/scale-in, give in-flight requests 30 s to finish before the target
  # is removed (the default is 300 s, which just makes deploys slow).
  deregistration_delay = 30

  # Liveness (/healthz), not readiness (/readyz): ECS *replaces* tasks that the ALB
  # marks unhealthy, so checking Postgres/Redis here would turn a short database
  # outage into a restart storm. During an outage the tasks stay up and answer
  # 503 problem+json; /readyz is used by the deploy smoke test instead.
  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# Port 80: redirect to HTTPS when a certificate is configured, otherwise serve the
# API directly over HTTP (fine for a demo, not for real users).
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = local.https_enabled ? "redirect" : "forward"
    target_group_arn = local.https_enabled ? null : aws_lb_target_group.api.arn

    dynamic "redirect" {
      for_each = local.https_enabled ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count = local.https_enabled ? 1 : 0

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06" # TLS 1.2+ only

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
