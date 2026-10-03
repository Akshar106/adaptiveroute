# Application secrets in Secrets Manager. ECS injects them into the containers as
# environment variables at task start (see "secrets" in ecs.tf); they never appear
# in the task definition itself.
#
# random_password values are stored in the Terraform state, which is why the state
# bucket must be private and encrypted. Rotation is future work (see README).
#
# Passwords use letters and digits only so they can be embedded in URLs without
# escaping; 32+ random alphanumerics is ~190+ bits of entropy.

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "redis_auth_token" {
  length  = 64 # ElastiCache accepts 16-128 characters
  special = false
}

resource "random_password" "api_key_pepper" {
  length  = 64
  special = false
}

locals {
  redis_host = aws_elasticache_replication_group.redis.primary_endpoint_address
  # "rediss" = Redis over TLS. The empty username before ":" is the default user.
  redis_base = "rediss://:${random_password.redis_auth_token.result}@${local.redis_host}:6379"
}

# --- AR_DATABASE_URL ---------------------------------------------------------------

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.name}/database-url"
  description             = "AR_DATABASE_URL: SQLAlchemy URL for PostgreSQL, including the password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql+asyncpg://%s:%s@%s:%d/%s",
    aws_db_instance.postgres.username,
    random_password.db.result,
    aws_db_instance.postgres.address,
    aws_db_instance.postgres.port,
    aws_db_instance.postgres.db_name,
  )
}

# --- AR_REDIS_URL ------------------------------------------------------------------

resource "aws_secretsmanager_secret" "redis_url" {
  name                    = "${local.name}/redis-url"
  description             = "AR_REDIS_URL: rediss:// URL with the ElastiCache auth token"
  recovery_window_in_days = 7
}

# ssl_cert_reqs=required makes redis-py verify the server certificate. The app
# derives Celery's broker (DB 1) and result backend (DB 2) from this URL, keeping the
# query string, and enables certificate verification for Celery on rediss://.
resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id     = aws_secretsmanager_secret.redis_url.id
  secret_string = "${local.redis_base}/0?ssl_cert_reqs=required"
}

# --- AR_API_KEY_PEPPER -------------------------------------------------------------

resource "aws_secretsmanager_secret" "api_key_pepper" {
  name                    = "${local.name}/api-key-pepper"
  description             = "AR_API_KEY_PEPPER: HMAC key for API-key hashes (changing it invalidates every key)"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "api_key_pepper" {
  secret_id     = aws_secretsmanager_secret.api_key_pepper.id
  secret_string = random_password.api_key_pepper.result
}

# --- GROQ_API_KEY ------------------------------------------------------------------
#
# Created empty: Terraform never sees the value, so it is not in the state. Set it
# once after the first apply (ECS tasks cannot start until it has a value):
#
#   aws secretsmanager put-secret-value \
#     --secret-id adaptiveroute-prod/groq-api-key --secret-string 'gsk_...'
resource "aws_secretsmanager_secret" "groq_api_key" {
  name                    = "${local.name}/groq-api-key"
  description             = "GROQ_API_KEY: set manually with aws secretsmanager put-secret-value"
  recovery_window_in_days = 7
}

locals {
  # Every secret the tasks may read; the execution role is limited to exactly these.
  app_secret_arns = [
    aws_secretsmanager_secret.database_url.arn,
    aws_secretsmanager_secret.redis_url.arn,
    aws_secretsmanager_secret.api_key_pepper.arn,
    aws_secretsmanager_secret.groq_api_key.arn,
  ]
}
