# Redis 7: Celery broker (DB 1) and result backend (DB 2), plus the app's rate
# limiter, idempotency keys and caches (DB 0). All of it is rebuildable, so there
# are no snapshots; PostgreSQL is the system of record.

resource "aws_elasticache_subnet_group" "this" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}

# Same policy as docker-compose: when memory is full, reject writes instead of
# silently evicting keys (an evicted idempotency key or queued task is a bug that
# is very hard to trace; an OOM error is not).
resource "aws_elasticache_parameter_group" "redis7" {
  name   = "${local.name}-redis7"
  family = "redis7"

  parameter {
    name  = "maxmemory-policy"
    value = "noeviction"
  }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = local.name
  description          = "AdaptiveRoute broker, results and cache"

  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  parameter_group_name = aws_elasticache_parameter_group.redis7.name
  port                 = 6379

  # A single node (no replica). Failover would need a second node, which doubles
  # the cost; a Redis restart only loses rebuildable state.
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  # Encrypted on disk and on the wire (clients must use rediss://), and every
  # client must present the auth token (generated in secrets.tf).
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis_auth_token.result

  snapshot_retention_limit = 0
  maintenance_window       = "sun:08:30-sun:09:30" # UTC
  apply_immediately        = true
}
