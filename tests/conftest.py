import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from incident_agents import tracing

EXPORTER = InMemorySpanExporter()
tracing.configure(exporter=EXPORTER)


@pytest.fixture
def spans():
    EXPORTER.clear()
    return EXPORTER
