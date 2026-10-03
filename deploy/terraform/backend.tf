# Remote state lives in S3. The block is deliberately empty ("partial configuration")
# so no account-specific bucket name is committed; pass it at init time:
#
#   terraform init \
#     -backend-config="bucket=adaptiveroute-tfstate-<account-id>" \
#     -backend-config="key=prod/terraform.tfstate" \
#     -backend-config="region=us-east-1" \
#     -backend-config="encrypt=true" \
#     -backend-config="use_lockfile=true"
#
# use_lockfile=true makes Terraform take a lock by writing a .tflock object next to
# the state file (S3 conditional writes), so no DynamoDB table is needed. It requires
# Terraform 1.10+ (GA in 1.11). The state contains generated passwords, so the bucket
# must be private, encrypted and versioned (README.md shows how to create it).
terraform {
  backend "s3" {}
}
