# Provider configuration and values shared by every other file.

provider "aws" {
  region = var.aws_region

  # Every taggable resource gets these tags, which makes cost allocation and
  # "what created this?" questions easy to answer in the console.
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Prefix for resource names, e.g. "adaptiveroute-prod".
  name = "${var.project_name}-${var.environment}"

  # A "dev" environment is meant to be created and destroyed freely: no final DB
  # snapshot, secrets deleted immediately, ECR emptied on destroy.
  ephemeral  = var.environment == "dev"
  account_id = data.aws_caller_identity.current.account_id

  # Two AZs: the minimum the ALB and the RDS subnet group accept. Named
  # explicitly (rather than "the first two available") so the subnet layout can
  # never shift if AWS adds a zone or one is temporarily impaired.
  azs = ["${var.aws_region}a", "${var.aws_region}b"]

  https_enabled = var.certificate_arn != ""

  # The URL users (and the deploy smoke test) hit. With a certificate you must
  # supply your own domain (variables.tf enforces it); without one we fall back
  # to plain HTTP on the ALB's DNS name.
  public_url = var.public_url != "" ? trimsuffix(var.public_url, "/") : "http://${aws_lb.this.dns_name}"
}
