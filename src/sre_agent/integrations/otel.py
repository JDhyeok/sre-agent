"""OpenTelemetry integration for the SRE Agent system.

Provides tracing for agent invocations, MCP tool calls, and overall
analysis workflow. Traces can be exported to any OTel-compatible backend
(Jaeger, Zipkin, OTLP collector, etc.).

Requires: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)

_tracer = None


def setup_tracing(
    service_name: str = "sre-agent",
    otlp_endpoint: str | None = None,
) -> None:
    """Initialize OpenTelemetry tracing.

    Args:
        service_name: Name of the service in traces
        otlp_endpoint: OTLP collector endpoint (default: OTEL_EXPORTER_OTLP_ENDPOINT env var)
    """
    global _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        logger.warning(
            "OpenTelemetry SDK not installed. Tracing disabled. "
            "Install with: pip install opentelemetry-api opentelemetry-sdk"
        )
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTLP exporter configured: %s", endpoint)
        except ImportError:
            logger.warning("OTLP exporter not installed. Using console exporter.")
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        if os.environ.get("SRE_AGENT_TRACE_CONSOLE"):
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("sre-agent")
    logger.info("OpenTelemetry tracing initialized for service: %s", service_name)


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Generator:
    """Create a trace span for an operation.

    Usage:
        with trace_span("agent.prometheus", {"incident_id": "INC-123"}):
            # ... do work ...
    """
    if _tracer is None:
        yield
        return

    with _tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, str(value) if not isinstance(value, (str, int, float, bool)) else value)
        try:
            yield span
        except Exception as e:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))
            raise
