# VPC across two AZs:
#   public subnets  - the ALB and the NAT gateway (they need internet-facing IPs)
#   private subnets - ECS tasks, RDS and Redis (nothing here is reachable from the internet)
#
# Tasks still need outbound internet (Groq API, ECR, Secrets Manager, CloudWatch,
# X-Ray, AMP), which goes through the NAT gateway.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.21.0" # exact pin: registry modules are not covered by the lock file

  name = local.name
  cidr = var.vpc_cidr
  azs  = local.azs

  # 10.0.0.0/24, 10.0.1.0/24 (public) and 10.0.10.0/24, 10.0.11.0/24 (private).
  public_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, i + 10)]

  # ONE NAT gateway for both AZs. A NAT gateway costs ~$33/month before data, so
  # one-per-AZ would double that. The trade-off: if the NAT's AZ fails, tasks in
  # the other AZ lose outbound internet (inbound traffic via the ALB still works).
  # For a single-developer project that is an acceptable risk.
  enable_nat_gateway = true
  single_nat_gateway = true

  # Required for private DNS names (RDS and ElastiCache endpoints).
  enable_dns_hostnames = true
  enable_dns_support   = true

  # The module also takes over the VPC's default security group and removes all of
  # its rules (manage_default_security_group defaults to true), so nothing can
  # accidentally use it.
}

# Gateway endpoint for S3: free, and ECR stores image layers in S3, so image pulls
# go through it instead of the NAT gateway (which charges per GB processed).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids

  tags = { Name = "${local.name}-s3" }
}
