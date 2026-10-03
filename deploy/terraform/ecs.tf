# ECS on Fargate: no servers to patch, pay per task-second.
#
# Three long-running services share one image and differ only in their command:
#   api    - FastAPI behind the ALB, autoscaled on CPU (2-6 tasks)
#   worker - Celery worker, autoscaled on CPU (1-4 tasks)
#   beat   - Celery beat scheduler, exactly 1 task, ever
# Database migrations run as a one-off task from the worker task definition with
# the command overridden (see .github/workflows/deploy.yml).
#
# Image ownership: Terraform creates the task definitions with a placeholder tag;
# the deploy workflow registers new revisions with the real image (commit SHA) and
# points the services at them. That is why the services ignore task_definition
# changes below. After changing anything task-related here (env vars, sizes, ...),
# re-run the Deploy workflow so the services pick it up.

resource "aws_ecs_cluster" "this" {
  name = local.name

  # Per-service/task CPU, memory and network metrics in CloudWatch.
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  app_container = "app" # the workflow swaps the image of the container with this name

  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  # Non-secret settings shared by every process.
  common_environment = [
    { name = "AR_ENV", value = "prod" }, # app mode (enables prod-only checks), independent of var.environment
    { name = "AR_LOG_JSON", value = "true" },
    { name = "AR_OTEL_ENABLED", value = "true" },
    { name = "AR_OTEL_EXPORTER_ENDPOINT", value = "http://localhost:4317" }, # ADOT sidecar
    # 10% head sampling: the local load test measured ~30% lower throughput with
    # every request traced (benchmarks/loadtest/README.md).
    { name = "AR_OTEL_SAMPLE_RATIO", value = "0.1" },
    { name = "AR_CORS_ORIGINS", value = local.public_url },
    { name = "AR_EMBEDDING_BACKEND", value = "fastembed" }, # model is baked into the image
    { name = "AR_TRACE_QUERY_URL", value = "" },            # no Jaeger on AWS; traces are in X-Ray
  ]

  # Injected by ECS from Secrets Manager when the task starts. "arn:key::" picks
  # one key out of a JSON secret.
  common_secrets = [
    { name = "AR_DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    { name = "AR_REDIS_URL", valueFrom = aws_secretsmanager_secret.redis_url.arn },
    { name = "AR_API_KEY_PEPPER", valueFrom = aws_secretsmanager_secret.api_key_pepper.arn },
    { name = "GROQ_API_KEY", valueFrom = aws_secretsmanager_secret.groq_api_key.arn },
  ]

  # PostgreSQL connection budget. db.t4g.micro allows roughly 80 connections
  # (max_connections ~ instance memory / 9.5 MB), and the app's default pool
  # (10 + 5 overflow per process) would exceed that at the baseline task count
  # alone: 2 api x 15 + 4 worker processes x 15 = 90. Per-process limits instead:
  #   api:    4 + 2 = 6 per task                  x 6 tasks max = 36
  #   worker: 2 per process x 4 processes = 8     x 4 tasks max = 32
  # Fully scaled out: 68, leaving headroom for migrations and admin sessions.
  # Raise these together with db_instance_class.
  services = {
    api = {
      cpu           = var.api_cpu
      memory        = var.api_memory
      command       = ["uvicorn", "adaptiveroute.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
      port_mappings = [{ containerPort = 8000, protocol = "tcp" }]
      metrics_port  = 8000
      stop_timeout  = 30
      environment = [
        { name = "AR_DB_POOL_SIZE", value = "4" },
        { name = "AR_DB_MAX_OVERFLOW", value = "2" },
        # --proxy-headers only trusts X-Forwarded-* from these addresses. The ALB
        # lives in the VPC, so trust the VPC range ("*" would make the client IP
        # spoofable, since uvicorn would then take the left-most, client-supplied entry).
        { name = "FORWARDED_ALLOW_IPS", value = var.vpc_cidr },
        # Keep idle connections open longer than the ALB's 120 s idle timeout, or
        # uvicorn (default 5 s) closes them under the ALB and clients see random 502s.
        { name = "UVICORN_TIMEOUT_KEEP_ALIVE", value = "130" },
      ]
    }

    worker = {
      cpu           = var.worker_cpu
      memory        = var.worker_memory
      command       = ["celery", "-A", "adaptiveroute.worker.celery_app", "worker", "--loglevel=INFO", "--concurrency=4"]
      port_mappings = []
      metrics_port  = 9100
      # Celery finishes in-flight tasks on SIGTERM ("warm shutdown"); give it the
      # Fargate maximum before SIGKILL. Tasks cut off anyway are redelivered
      # (acks_late).
      stop_timeout = 120
      environment = [
        { name = "AR_DB_POOL_SIZE", value = "2" },
        { name = "AR_DB_MAX_OVERFLOW", value = "0" },
        # Enables the multi-process Prometheus endpoint on :9100. The directory
        # must exist in the image and be writable by the app user.
        { name = "PROMETHEUS_MULTIPROC_DIR", value = "/tmp/prometheus" },
      ]
    }

    beat = {
      cpu           = var.beat_cpu
      memory        = var.beat_memory
      command       = ["celery", "-A", "adaptiveroute.worker.celery_app", "beat", "--loglevel=INFO", "--schedule=/tmp/celerybeat-schedule"]
      port_mappings = []
      metrics_port  = null # no metrics endpoint; the sidecar only runs a traces pipeline
      stop_timeout  = 30
      environment   = []
    }
  }
}

# --- logs --------------------------------------------------------------------------

# One log group per service, named after the task family (the deploy workflow
# relies on that to print migration logs). Streams: ecs/app/<task-id> and
# ecs/adot-collector/<task-id>.
resource "aws_cloudwatch_log_group" "service" {
  for_each = local.services

  name              = "/ecs/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

# --- task definitions --------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  for_each = local.services

  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc" # each task gets its own ENI and security group
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.task_execution.arn # used by ECS: pull image, read secrets, write logs
  task_role_arn            = aws_iam_role.task.arn           # used by the containers: X-Ray, AMP

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # matches the image CI builds on ubuntu-latest
  }

  container_definitions = jsonencode([
    {
      name         = local.app_container
      image        = local.image
      essential    = true # if the app exits, the task stops (this is what ends a migration task)
      command      = each.value.command
      portMappings = each.value.port_mappings
      environment  = concat(local.common_environment, each.value.environment)
      secrets      = local.common_secrets
      stopTimeout  = each.value.stop_timeout

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.service[each.key].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    },
    {
      name  = "adot-collector"
      image = var.adot_image
      # Non-essential: if the collector dies we lose telemetry, not the service.
      # ECS restarts just this container instead.
      essential = false
      restartPolicy = {
        enabled              = true
        restartAttemptPeriod = 60
      }
      environment = [
        { name = "AOT_CONFIG_CONTENT", value = local.adot_config[each.key] },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.service[each.key].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    },
  ])
}

# --- services ----------------------------------------------------------------------

resource "aws_ecs_service" "api" {
  name             = "api"
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.this["api"].arn
  desired_count    = var.api_desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false # outbound goes through the NAT gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = local.app_container
    container_port   = 8000
  }

  # Ignore ALB health checks for the first 90 s while the embedding model loads.
  health_check_grace_period_seconds = 90

  # Rolling deploy: start new tasks first (up to 200%), never drop below 100%.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # If new tasks keep failing to become healthy, stop the deploy and roll back to
  # the last working task definition automatically.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  propagate_tags          = "SERVICE"
  enable_ecs_managed_tags = true

  lifecycle {
    # task_definition: owned by the deploy workflow.
    # desired_count: owned by autoscaling after creation.
    ignore_changes = [task_definition, desired_count]
  }

  # The target group must be attached to a listener before a service can use it.
  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "worker" {
  name             = "worker"
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.this["worker"].arn
  desired_count    = var.worker_desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  propagate_tags          = "SERVICE"
  enable_ecs_managed_tags = true

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

resource "aws_ecs_service" "beat" {
  name             = "beat"
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.this["beat"].arn
  desired_count    = 1 # two schedulers would enqueue every periodic task twice
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.worker.id] # needs Redis (the broker), nothing inbound
    assign_public_ip = false
  }

  # Stop the old task BEFORE starting the new one (max 100%, min 0%), so there is
  # never a moment with two beat processes. The cost is a short gap in scheduling,
  # which is harmless for these periodic jobs.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  propagate_tags          = "SERVICE"
  enable_ecs_managed_tags = true

  lifecycle {
    ignore_changes = [task_definition]
  }
}

# --- autoscaling -------------------------------------------------------------------

locals {
  autoscaled_services = {
    api    = { service = aws_ecs_service.api.name, min = var.api_desired_count, max = var.api_max_count }
    worker = { service = aws_ecs_service.worker.name, min = var.worker_desired_count, max = var.worker_max_count }
  }
}

resource "aws_appautoscaling_target" "service" {
  for_each = local.autoscaled_services

  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${each.value.service}"
  min_capacity       = each.value.min
  max_capacity       = each.value.max
}

# Target tracking: AWS adds/removes tasks to keep average CPU near 60%.
# For the worker this is a proxy: LLM calls are mostly waiting on the network, so
# a growing queue does not always show up as CPU. Queue depth would be the better
# signal (it needs a custom metric).
resource "aws_appautoscaling_policy" "cpu" {
  for_each = local.autoscaled_services

  name               = "${local.name}-${each.key}-cpu60"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.service[each.key].service_namespace
  scalable_dimension = aws_appautoscaling_target.service[each.key].scalable_dimension
  resource_id        = aws_appautoscaling_target.service[each.key].resource_id

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 60
    scale_out_cooldown = 60  # react quickly to load
    scale_in_cooldown  = 300 # remove capacity slowly
  }
}
