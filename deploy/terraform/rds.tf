# PostgreSQL 16 with pgvector. RDS ships the extension; the first Alembic
# migration runs CREATE EXTENSION IF NOT EXISTS vector as the master user.

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}

# Created up front so the PostgreSQL log export below gets a retention period
# (RDS would otherwise create the group with "never expire").
resource "aws_cloudwatch_log_group" "rds" {
  name              = "/aws/rds/instance/${local.name}/postgresql"
  retention_in_days = var.log_retention_days
}

resource "aws_db_instance" "postgres" {
  identifier = local.name

  engine = "postgres"
  # Major version only: RDS picks the current 16.x and applies minor upgrades in
  # the maintenance window.
  engine_version             = "16"
  auto_minor_version_upgrade = true
  instance_class             = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 50 # grow automatically instead of filling up
  storage_type          = "gp3"
  storage_encrypted     = true # AWS-managed KMS key

  db_name  = "adaptiveroute"
  username = "adaptiveroute"
  # Generated in secrets.tf and stored (inside AR_DATABASE_URL) in Secrets Manager.
  # Not an RDS-managed master password: RDS would rotate it, and the URL secret the
  # app reads would go stale.
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  # Single-AZ to keep cost down (Multi-AZ doubles the instance price). Point-in-
  # time restore from the 7 days of automated backups is the recovery story.
  multi_az                = false
  backup_retention_period = var.db_backup_retention_days
  backup_window           = "07:00-07:30"         # UTC
  maintenance_window      = "sun:07:45-sun:08:30" # UTC, after the backup
  copy_tags_to_snapshot   = true

  enabled_cloudwatch_logs_exports = ["postgresql"]

  deletion_protection = var.deletion_protection
  # Keep a final snapshot on destroy, except for throwaway dev environments.
  skip_final_snapshot       = var.environment == "dev"
  final_snapshot_identifier = var.environment == "dev" ? null : "${local.name}-final"

  # PostgreSQL 15+ default parameter groups already set rds.force_ssl = 1, so
  # every connection is TLS-encrypted.

  depends_on = [aws_cloudwatch_log_group.rds]
}
