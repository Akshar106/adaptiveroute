terraform {
  # 1.9 is the floor for the configuration itself (cross-variable validation in
  # variables.tf). The S3 backend's native lockfile (`use_lockfile`) needs 1.10+,
  # and is GA from 1.11, so use 1.11+ for real init/plan/apply (see backend.tf).
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
