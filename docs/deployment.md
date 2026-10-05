# Deployment guide

## 1. Local: Docker Compose

Prerequisites: Docker (Compose v2.24+). Optional: a Groq API key, which agents and the
LLM router need. Without one, routing still works, but execution returns `failed`
with a clear error.

```bash
cp -n .env.example .env         # -n: never overwrite an existing .env; put GROQ_API_KEY=... in .env
make up                         # build the image, run migrations, start everything
make api-key                    # prints an admin API key once - copy it
open http://localhost:8000      # dashboard: paste the key in the header field
```

| Service | Port | Notes |
|---|---|---|
| api (+ dashboard, `/docs`) | `${AR_API_PORT:-8000}` | set `AR_API_PORT=8080` if 8000 is taken |
| worker | `9100` (metrics; internal) | Celery prefork, concurrency 4 |
| beat | none | scheduler, exactly one instance |
| migrate | none | one-shot `alembic upgrade head`; api and worker wait for it |
| postgres | 5432 | `pgvector/pgvector:pg17` |
| redis | 6379 | AOF persistence, `noeviction` (broker safety) |
| jaeger | 16686 (UI), 4317 (OTLP) | in-memory trace storage |
| prometheus | 9090 | scrapes api and worker every 10 s |
| grafana | 3000 | anonymous viewer; dashboard provisioned |

Useful commands:

```bash
make logs                                      # follow api/worker/beat logs (JSON)
docker compose exec api adaptiveroute --help   # CLI inside the container
docker compose exec api adaptiveroute bench import benchmarks/results/<run>/results.json
docker compose exec api adaptiveroute bench seed-history   # give the adaptive router history (demo)
make down                                      # stop (keeps volumes); add -v to wipe data
```

### Developing without containers

```bash
make install            # uv sync (Python 3.12)
make deps-up            # postgres + redis only
make migrate
make run-api            # uvicorn --reload on :8000 (PORT=8010 make run-api to change)
make run-worker         # celery worker (solo pool on macOS)
cd frontend && npm install && npm run dev    # dashboard on :5173, proxies /v1 to :8000
make test               # unit tests; `make test-all` adds integration tests + coverage
```

> **macOS note:** if the repository lives in an iCloud-synced folder (e.g. Desktop),
> macOS marks dotfiles inside it as hidden, and Python then ignores the virtualenv's
> `.pth` files. The Makefile exports `PYTHONPATH=src` to sidestep this. If you call
> `uv run` directly, prefix it with `PYTHONPATH=src`.

## 2. Production: AWS (ECS Fargate)

The infrastructure is defined in [`deploy/terraform/`](../deploy/terraform/); see its
README for bootstrap and per-resource details. Summary:

| Concern | AWS service |
|---|---|
| Compute | ECS Fargate: `api` (behind an ALB, autoscaled 2–6 on CPU), `worker` (1–4), `beat` (exactly 1). The ALB health-checks `/healthz` (liveness), because ECS replaces unhealthy targets, so a readiness check there would cause restart storms during a DB outage. |
| Database | RDS PostgreSQL 16 (pgvector), encrypted, 7-day backups, private subnets |
| Cache / broker | ElastiCache Redis 7, TLS + auth token, private subnets |
| Images | ECR (immutable tags, scan on push) |
| Secrets | Secrets Manager: database URL, Redis URL, API-key pepper, Groq key |
| Traces / metrics | ADOT collector sidecar → X-Ray (10% head sampling) and Amazon Managed Prometheus; dashboards from `deploy/grafana/dashboards` can be imported into any Grafana pointed at AMP. The dashboard's trace view needs Jaeger, so on AWS use the X-Ray console. |
| Logs | CloudWatch Logs (14-day retention) |
| CI/CD | GitHub Actions → AWS via OIDC (no long-lived keys) |

### Release pipeline

1. **CI** (`.github/workflows/ci.yml`) runs on every push and PR:
   - lint, format and mypy;
   - an OpenAPI drift check;
   - migrations up, `alembic check`, down and up again;
   - unit and integration tests with coverage (Postgres + Redis service containers);
   - frontend lint, tests and build;
   - a Docker build, then an end-to-end smoke test against the full Compose stack
     (health, readiness, authenticated route-only query, metrics).
2. **CD** (`.github/workflows/deploy.yml`) runs on a successful CI run on `main`:
   1. build and push the image tagged with the commit SHA;
   2. register new task definitions;
   3. run `alembic upgrade head` as a one-off Fargate task, and stop if it fails;
   4. update the api, worker and beat services;
   5. wait for them to stabilise;
   6. smoke-test `/readyz`.

   The ECS deployment circuit breaker rolls back automatically if new tasks fail
   their health checks.

### First deployment checklist

1. Bootstrap the Terraform state bucket, then `terraform init` / `plan` / `apply`
   (see `deploy/terraform/README.md`).
2. Set the Groq key **before the first deploy**, because ECS cannot start tasks while a
   referenced secret has no value:
   `aws secretsmanager put-secret-value --secret-id adaptiveroute-prod/groq-api-key --secret-string <key>`.
3. Set the GitHub repository variables listed at the top of `deploy.yml`.
   `terraform output github_variables` prints all of them, ready for `gh variable set`.
4. Push to `main`. CI runs, then CD deploys.
5. Create the first admin API key with a one-off task running
   `adaptiveroute create-api-key --name admin --role admin`. Read the key from that
   task's CloudWatch logs; it is printed once.

### Operations

- **Rollback:** re-run the deploy workflow on an earlier commit
  (`workflow_dispatch`), or point the service at the previous task-definition
  revision. Migrations are additive (expand/contract), so the previous image still
  works with the new schema.
- **Scaling:** adjust the min/max task variables, or the autoscaling target.
- **Cost:** roughly $195–210/month at baseline task counts (estimate; assumptions in
  `deploy/terraform/README.md`). Fargate, the NAT gateway and the ALB dominate. Scale
  services to 0 between demos.

> Status: the Terraform has been validated (`terraform validate`, `fmt`, checkov) but
> not applied to a real AWS account as part of this repository's history.
