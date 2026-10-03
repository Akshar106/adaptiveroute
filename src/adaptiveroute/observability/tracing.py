"""OpenTelemetry tracing setup.

Spans are exported over OTLP/gRPC (to Jaeger locally, to the ADOT collector -> X-Ray
on AWS). Auto-instrumentation covers FastAPI, SQLAlchemy, Redis, httpx and Celery;
the routing and agent code adds its own spans with ``ar.*`` and ``gen_ai.*``
attributes (strategy, chosen agent, model, tokens, cost).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from adaptiveroute import __version__
from adaptiveroute.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI

_configured = False


def configure_tracing(settings: Settings, service_name: str) -> None:
    """Install the global tracer provider + library instrumentation (idempotent)."""
    global _configured
    if not settings.otel_enabled or _configured:
        return
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": __version__,
            "deployment.environment": settings.env,
        }
    )
    provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio))
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint, insecure=True)
        )
    )
    trace.set_tracer_provider(provider)

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(enable_commenter=False)
    _configured = True


def instrument_app(app: FastAPI, settings: Settings) -> None:
    if not settings.otel_enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
