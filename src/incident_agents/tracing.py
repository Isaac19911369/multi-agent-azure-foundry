"""OpenTelemetry tracing with GenAI semantic conventions.

Spans: ``invoke_agent <name>`` → ``chat <model>`` and ``execute_tool <name>``. When
``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set (the Application Insights resource
connected to the Azure AI Foundry project), traces are exported to Azure Monitor and
appear in the Foundry tracing view.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

_configured = False


def configure(console: bool = False, exporter: SpanExporter | None = None) -> None:
    global _configured
    if _configured:
        return
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if conn and exporter is None:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn)
    else:
        provider = TracerProvider()
        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        elif console:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
    _configured = True


def tracer() -> trace.Tracer:
    return trace.get_tracer("incident_agents")
