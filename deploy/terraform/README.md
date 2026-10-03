# AdaptiveRoute on AWS (Terraform)

Production infrastructure for one developer: ECS Fargate behind an ALB, RDS PostgreSQL
(pgvector), ElastiCache Redis, Secrets Manager, CloudWatch Logs, X-Ray and Amazon Managed
Prometheus. No Kubernetes, no service mesh.

```
internet ─► ALB (public subnets) ─► api tasks ──┬─► RDS PostgreSQL 16
                                                ├─► ElastiCache Redis 7 (TLS + auth)
             worker / beat / one-off tasks ─────┘
             (private subnets, outbound via 1 NAT gateway)

every task = app container + ADOT sidecar ─► X-Ray (traces), AMP (metrics)
```

| File | What it holds |
|---|---|
| `versions.tf`, `backend.tf`, `main.tf` | Terraform/provider versions, S3 state, provider + shared locals |
| `variables.tf`, `terraform.tfvars.example` | Inputs (only `github_repo` is required) |
| `network.tf` | VPC, 2 AZs, 1 NAT gateway, free S3 gateway endpoint |
| `security_groups.tf` | ALB → api → RDS/Redis; worker/beat have no inbound |
| `alb.tf` | ALB, HTTP(→HTTPS) listeners, target group with a `/healthz` (liveness) check |
| `ecs.tf` | Cluster, task definitions (app + ADOT sidecar), 3 services, CPU autoscaling |
| `rds.tf`, `redis.tf`, `secrets.tf` | Data stores and the secrets ECS injects into the tasks |
| `iam.tf`, `github_oidc.tf` | Task roles; GitHub OIDC deploy role (main branch only) |
| `observability.tf`, `adot-config.yaml.tftpl` | AMP workspace; collector config per service |
| `outputs.tf` | URLs, names and `github_variables` for the deploy workflow |

## 1. Bootstrap the state bucket (once per account)

Use Terraform **1.11+** for real runs: `use_lockfile` (S3-native state locking, no
DynamoDB table) arrived in 1.10 and is GA in 1.11.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="adaptiveroute-tfstate-$ACCOUNT_ID"
aws s3api create-bucket --bucket "$BUCKET" --region us-east-1
#   (outside us-east-1 add: --create-bucket-configuration LocationConstraint=<region>)
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

New buckets are encrypted (SSE-S3) by default. The state holds the generated DB password,
Redis token and pepper, so keep the bucket private.

## 2. Init, plan, apply

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # set github_repo (and HTTPS settings if any)

terraform init \
  -backend-config="bucket=$BUCKET" \
  -backend-config="key=prod/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="encrypt=true" \
  -backend-config="use_lockfile=true"
terraform plan -out tfplan
terraform apply tfplan
```

The first apply takes about 15 minutes (RDS and ElastiCache). The ECS services are created
but **cannot start yet**: there is no image and the Groq secret is empty. That's expected.

HTTPS: request an ACM certificate for your domain, set `certificate_arn` and
`public_url = "https://api.example.com"`, apply, then point a DNS CNAME/alias record at
`terraform output alb_dns_name`.

## 3. Set the Groq API key

The secret is created without a value, so the key never enters the Terraform state. ECS
cannot start any task (not even migrations) until it has one:

```bash
aws secretsmanager put-secret-value \
  --secret-id adaptiveroute-prod/groq-api-key --secret-string 'gsk_...'
```

## 4. Connect GitHub and deploy

Copy the outputs into repository variables (needs the `gh` CLI):

```bash
terraform output -json github_variables | jq -r 'to_entries[] | "\(.key)\t\(.value)"' |
  while IFS=$'\t' read -r k v; do gh variable set "$k" --body "$v" --repo OWNER/NAME; done
```

Then push to `main` (CI passes → Deploy runs) or start **Actions → Deploy → Run workflow**.
The workflow builds the image (tag = commit SHA), registers new task-definition revisions,
runs `alembic upgrade head` as a one-off task, rolls the services and curls `/readyz`.

Terraform creates the task definitions; the workflow only swaps the image. After you change
anything task-related in Terraform (env vars, CPU/memory, sidecar), apply and then re-run
the Deploy workflow so the services pick it up.

## 5. Create the first admin API key

Run the CLI as a one-off task using the worker task definition:

```bash
CLUSTER=$(terraform output -raw ecs_cluster_name)
FAMILY=$(terraform output -json task_definition_families | jq -r .worker)
LOG_GROUP=$(terraform output -json log_groups | jq -r .worker)
SUBNETS=$(terraform output -json private_subnet_ids | jq -r 'join(",")')
SG=$(terraform output -raw task_security_group_id)

TASK_ARN=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$FAMILY" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"app","command":["adaptiveroute","create-api-key","--name","admin","--role","admin"]}]}' \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"

STREAM="ecs/app/${TASK_ARN##*/}"
aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "$STREAM" \
  --query 'events[].message' --output text
```

The key is printed **once**; only its HMAC is stored. Save it in a password manager, then
delete the log stream so the plaintext key doesn't sit in CloudWatch for 14 days:
`aws logs delete-log-stream --log-group-name "$LOG_GROUP" --log-stream-name "$STREAM"`.
Further keys can be created through `POST /v1/admin/api-keys`.

## Operations

- **Logs:** `aws logs tail /ecs/adaptiveroute-prod-api --follow` (also `-worker`, `-beat`).
- **Rollback:** `aws ecs update-service --cluster ... --service api --task-definition <family>:<older revision>`.
  ECR keeps the last 20 images. Migrations are not rolled back, so keep them backwards compatible.
- **Traces:** X-Ray console (CloudWatch → Traces). The dashboard's trace waterfall
  (`/v1/traces/{id}`) proxies Jaeger, so it returns 503 on AWS (`AR_TRACE_QUERY_URL` is empty).
- **Destroy:** set `deletion_protection = false`, apply, delete the images in ECR, then
  `terraform destroy`. A final RDS snapshot is kept unless `environment = "dev"`.

## Grafana dashboards on Amazon Managed Prometheus

Amazon Managed Grafana is left out because it requires IAM Identity Center. Any Grafana
works; it needs an AWS identity allowed to query AMP (managed policy
`AmazonPrometheusQueryAccess`) and the workspace URL from `terraform output amp_endpoint`.

The dashboards in `deploy/grafana/dashboards/*.json` reference the data source
**uid `prometheus`**, so give the AMP data source that uid and they import unchanged.

Self-hosted (e.g. `docker run grafana/grafana` on your laptop with AWS credentials in the
environment and `GF_AUTH_SIGV4_AUTH_ENABLED=true`), provision:

```yaml
apiVersion: 1
datasources:
  - name: Amazon Managed Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: https://aps-workspaces.us-east-1.amazonaws.com/workspaces/ws-xxxxxxxx/  # amp_endpoint
    isDefault: true
    jsonData:
      httpMethod: POST
      sigV4Auth: true
      sigV4AuthType: default   # credentials from env / ~/.aws / instance role
      sigV4Region: us-east-1
```

and mount `deploy/grafana/dashboards` with the existing dashboard provider. Amazon Managed
Grafana: add AMP through *AWS data sources*, set the uid to `prometheus` (or pick the AMP
data source in the import dialog), then **Dashboards → New → Import** each JSON file.

Metrics carry `job` = `adaptiveroute-api` / `adaptiveroute-worker` and `instance` = the ECS
task ARN (each task scrapes itself, so the task ARN keeps series apart).

## Monthly cost (estimate)

**Estimate only.** Assumptions: us-east-1 on-demand list prices, 730 h/month, baseline task
counts (2 api, 1 worker, 1 beat, no scale-out), low traffic, ~20 GB through the NAT,
~1,700 active metric series scraped every 60 s, X-Ray within the free tier. Check against
the AWS Pricing Calculator before relying on it.

| Resource | Basis | ≈ USD/month |
|---|---|---|
| Fargate api | 2 × (0.5 vCPU, 1 GB) | 36 |
| Fargate worker | 1 × (1 vCPU, 2 GB) | 36 |
| Fargate beat | 1 × (0.25 vCPU, 0.5 GB) | 9 |
| NAT gateway | $0.045/h + $0.045/GB | 34 |
| Public IPv4 addresses | NAT + 2 ALB nodes × $0.005/h | 11 |
| ALB | $0.0225/h + ~1 LCU | 22 |
| RDS db.t4g.micro | single-AZ + 20 GB gp3 (backups ≤ DB size are free) | 14 |
| ElastiCache cache.t4g.micro | 1 node | 12 |
| Container Insights | custom metrics per cluster/service/family (least certain line) | 10–25 |
| Amazon Managed Prometheus | ~75 M samples ingested × $0.90/10 M | 7 |
| Secrets Manager | 4 secrets × $0.40 | 1.60 |
| CloudWatch Logs | ~3 GB ingested × $0.50 | 2 |
| ECR, S3 state, X-Ray | storage / free tier | 1 |
| **Total** | | **≈ 195–210** |

Scaling to the maximums adds about $180 (4 more api tasks ≈ $72, 3 more workers ≈ $108).
Biggest levers if that's too much: scale services to 0 when not demoing, Graviton
(`ARM64`, ~20% cheaper Fargate, needs an arm64 image), Fargate Spot for the worker
(~70% cheaper; Celery's `acks_late` makes interruptions safe), or turn off Container
Insights.

## Security scan (checkov): accepted findings

`checkov -d deploy/terraform` passes 200+ checks. These findings are accepted on purpose:

| Check | Finding | Why it's accepted |
|---|---|---|
| CKV_AWS_2, CKV_AWS_260 | HTTP listener, port 80 open to the internet | It's a public web endpoint. With `certificate_arn` set, port 80 only redirects to HTTPS. Plain HTTP is only used when no domain exists. |
| CKV_AWS_378 | Target group uses HTTP | TLS ends at the ALB; ALB → task traffic stays inside the VPC. |
| CKV_AWS_91 | No ALB access logs | Needs an S3 bucket and bucket policy; the app already logs every request (JSON) to CloudWatch. |
| CKV2_AWS_28 | No WAF | ≥ $5/month plus rules. The API needs an API key and is rate-limited per key. |
| CKV_AWS_136, CKV_AWS_149, CKV_AWS_158, CKV_AWS_191 | No customer-managed KMS keys (ECR, Secrets Manager, logs, ElastiCache) | All of it is encrypted at rest with AWS-managed keys. A CMK costs $1/month per key plus key policies and only adds cross-account control and key-level auditing, which this project doesn't need. |
| CKV_AWS_338 | Log retention < 1 year | 14 days on purpose (cost); no compliance requirement. |
| CKV2_AWS_57 | No automatic secret rotation | Future work. The DB password is part of the `AR_DATABASE_URL` secret, so rotation needs a custom Lambda that rewrites the URL (RDS-managed rotation would break it). |
| CKV_AWS_157, CKV2_AWS_50 | RDS not Multi-AZ, Redis without failover | Each would double that line of the bill. RDS has 7 days of point-in-time recovery; Redis holds only rebuildable state. |
| CKV_AWS_118, CKV_AWS_353 | No Enhanced Monitoring or Performance Insights | Enhanced Monitoring needs another IAM role and adds cost; Performance Insights is being replaced by CloudWatch Database Insights. Standard RDS metrics and the PostgreSQL log export are on. |
| CKV_AWS_161 | No IAM database authentication | The app connects with a password URL. IAM auth would need token-refresh code in the app. |
| CKV2_AWS_30 | No query logging | `log_statement=all` would write every query, including user text, to the logs. Errors are still exported. |
| CKV_TF_1 | Module not pinned to a git commit | The registry module is pinned to the exact version `5.21.0`. |
| CKV_AWS_358 | GitHub OIDC trust "unsafe claims" | False positive: checkov can't resolve `var.github_repo` (it has no default). The subject is `repo:<owner>/<name>:ref:refs/heads/main`, the strictest form. |
| CKV_SECRET_4 | "Basic auth credentials" in `secrets.tf` | False positive: it's the `%s:%s@` format string that builds `AR_DATABASE_URL`; there is no literal credential. |

Also deliberate (checkov doesn't flag them): no VPC flow logs (cost), no ECS Exec, and the
containers' root filesystem is writable (Celery beat and the Prometheus multiprocess
directory write to `/tmp`; making it read-only needs a `/tmp` volume and testing against
the image).

To get a clean run (e.g. in CI):

```bash
checkov -d deploy/terraform --skip-check CKV_AWS_2,CKV_AWS_260,CKV_AWS_378,CKV_AWS_91,CKV2_AWS_28,CKV_AWS_136,CKV_AWS_149,CKV_AWS_158,CKV_AWS_191,CKV_AWS_338,CKV2_AWS_57,CKV_AWS_157,CKV2_AWS_50,CKV_AWS_118,CKV_AWS_353,CKV_AWS_161,CKV2_AWS_30,CKV_TF_1,CKV_AWS_358,CKV_SECRET_4
```

## Future work

Secret rotation, Multi-AZ RDS and a Redis replica, WAF, CloudWatch alarms (5xx rate,
queue depth, DB CPU/connections), autoscaling the worker on queue depth instead of CPU,
RDS Proxy or a bigger instance class before raising the max task counts (see the
connection budget in `ecs.tf`).
