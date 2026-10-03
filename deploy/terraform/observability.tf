# Telemetry backends:
#   logs    -> CloudWatch Logs (awslogs driver, log groups in ecs.tf)
#   traces  -> AWS X-Ray (via the ADOT sidecar; nothing to create here)
#   metrics -> Amazon Managed Service for Prometheus (this workspace)
#
# Grafana is not created here: Amazon Managed Grafana requires IAM Identity Center.
# README.md shows how to point any Grafana at this workspace and import the
# dashboards from deploy/grafana/dashboards.

resource "aws_prometheus_workspace" "this" {
  alias = local.name
}

locals {
  amp_remote_write_url = "${aws_prometheus_workspace.this.prometheus_endpoint}api/v1/remote_write"

  # One collector config per service. metrics_port = null means "traces only"
  # (beat exposes no metrics endpoint).
  adot_config = {
    for name, svc in local.services : name => templatefile("${path.module}/adot-config.yaml.tftpl", {
      region           = var.aws_region
      job_name         = "${var.project_name}-${name}" # same job names as deploy/prometheus/prometheus.yml
      metrics_port     = svc.metrics_port
      scrape_interval  = "60s" # AMP bills per sample; 60 s halves the cost of 30 s
      remote_write_url = local.amp_remote_write_url
    })
  }
}
