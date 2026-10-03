"""Celery application: async query execution, periodic maintenance, benchmarks.

Reliability settings, for long LLM tasks:
* ``task_acks_late`` + ``task_reject_on_worker_lost``: a message is acknowledged only
  after the task finishes, so a crashed worker's task is redelivered. ``execute`` is
  safe to redeliver because it atomically claims the query (queued -> running).
* ``worker_prefetch_multiplier=1``: don't let one worker hoard slow tasks.
* ``visibility_timeout`` > hard time limit, so Redis doesn't redeliver running tasks.
"""

from __future__ import annotations

import os
from typing import Any

from celery import Celery
from celery.signals import setup_logging, worker_init, worker_process_shutdown

from adaptiveroute.config import get_settings
from adaptiveroute.observability.logs import configure_logging

settings = get_settings()

celery_app = Celery(
    "adaptiveroute",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["adaptiveroute.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=240,
    task_time_limit=300,
    broker_transport_options={"visibility_timeout": 600},
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    timezone="UTC",
    beat_schedule={
        "refresh-agent-stats": {
            "task": "adaptiveroute.refresh_agent_stats",
            "schedule": 60.0,
        },
        "recover-stuck-queries": {
            "task": "adaptiveroute.recover_stuck_queries",
            "schedule": 300.0,
        },
        "purge-idempotency-keys": {
            "task": "adaptiveroute.purge_idempotency_keys",
            "schedule": 3600.0,
        },
    },
)


@setup_logging.connect
def _configure_logging(**_: Any) -> None:
    # Connecting this signal stops Celery from replacing our logging configuration.
    configure_logging(settings.log_level, settings.log_json)


@worker_init.connect
def _start_metrics_server(**_: Any) -> None:
    """Expose Prometheus metrics aggregated across all prefork child processes."""
    if "PROMETHEUS_MULTIPROC_DIR" not in os.environ:
        return
    from prometheus_client import CollectorRegistry, multiprocess, start_http_server

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    start_http_server(int(os.environ.get("AR_WORKER_METRICS_PORT", "9100")), registry=registry)


@worker_process_shutdown.connect
def _mark_process_dead(pid: int | None = None, **_: Any) -> None:
    if "PROMETHEUS_MULTIPROC_DIR" in os.environ and pid is not None:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(pid)  # type: ignore[no-untyped-call]
