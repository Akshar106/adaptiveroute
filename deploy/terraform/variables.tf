# --- naming / placement --------------------------------------------------------

variable "project_name" {
  description = "Used in resource names and as the ECR repository name."
  type        = string
  default     = "adaptiveroute"

  validation {
    # "<project>-staging-api" must fit the 32-character limit on target group names.
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.project_name))
    error_message = "Use 2-20 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name (dev, staging or prod). dev skips the final RDS snapshot on destroy."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "aws_region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC. Subnets are /24s carved out of it."
  type        = string
  default     = "10.0.0.0/16"
}

# --- CI/CD -----------------------------------------------------------------------

variable "github_repo" {
  description = "GitHub repository allowed to deploy, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repo))
    error_message = "github_repo must look like owner/name."
  }
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub Actions OIDC provider. Set false if the account already has one (only one per URL is allowed)."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Image tag Terraform writes into the task definitions. Only used until the first CD deploy, which registers new revisions with the commit SHA."
  type        = string
  default     = "bootstrap"
}

# --- edge ------------------------------------------------------------------------

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS. Empty = serve plain HTTP on the ALB DNS name."
  type        = string
  default     = ""
}

variable "public_url" {
  description = "Public base URL, e.g. https://api.example.com (you create the DNS record). Empty = http://<alb dns name>."
  type        = string
  default     = ""

  # Cross-variable validation (Terraform 1.9+): an HTTPS certificate is only
  # useful with a domain name that matches it.
  validation {
    condition     = var.certificate_arn == "" || startswith(var.public_url, "https://")
    error_message = "When certificate_arn is set, public_url must be the https:// URL of a domain on that certificate."
  }
}

# --- capacity --------------------------------------------------------------------

variable "api_desired_count" {
  description = "API tasks at creation time; also the autoscaling minimum."
  type        = number
  default     = 2
}

variable "api_max_count" {
  description = "Autoscaling maximum for the API."
  type        = number
  default     = 6
}

variable "worker_desired_count" {
  description = "Celery worker tasks at creation time; also the autoscaling minimum."
  type        = number
  default     = 1
}

variable "worker_max_count" {
  description = "Autoscaling maximum for the Celery worker."
  type        = number
  default     = 4
}

# Fargate sizes (CPU units / MiB). Only certain combinations are valid, see
# https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html
variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

# The worker forks 4 processes and each loads the embedding model, so it needs
# more memory than the API.
variable "worker_cpu" {
  type    = number
  default = 1024
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "beat_cpu" {
  type    = number
  default = 256
}

variable "beat_memory" {
  type    = number
  default = 512
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "redis_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.micro"
}

# --- safety / ops ----------------------------------------------------------------

variable "deletion_protection" {
  description = "Protect the RDS instance and the ALB from deletion. Set false (and apply) before terraform destroy."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for container and RDS logs."
  type        = number
  default     = 14
}

variable "adot_image" {
  description = "AWS Distro for OpenTelemetry collector image (sidecar). Pinned so upgrades are deliberate."
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.50.0"
}
