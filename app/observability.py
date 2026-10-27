"""Observability (FR-18): every AI decision, tool call, gate evaluation, human
decision and stage transition is recorded as an event, and optionally exported
as OpenTelemetry spans when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is configured.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.config import Settings
from app.persistence import Store

log = logging.getLogger("sdlc.copilot")

EVENT_STAGE = "stage_transition"
EVENT_AGENT = "agent_call"
EVENT_TOOL = "tool_call"
EVENT_GATE = "gate_evaluation"
EVENT_HUMAN = "human_decision"
EVENT_MODIFICATION = "ai_modification"
EVENT_POLICY = "policy_decision"
EVENT_ERROR = "error"


class Tracer:
    """Thin wrapper so the orchestrator never depends on the OpenTelemetry API directly."""

    def __init__(self, settings: Settings):
        self._tracer = None
        if settings.otel_enabled:
            try:
                from opentelemetry import trace
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.resources import Resource
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                provider = TracerProvider(resource=Resource.create({"service.name": "sdlc-copilot"}))
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                trace.set_tracer_provider(provider)
                self._tracer = trace.get_tracer("sdlc-copilot")
            except Exception as exc:  # pragma: no cover - optional dependency
                log.warning("OpenTelemetry export disabled: %s", exc)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[None]:
        if self._tracer is None:
            yield
            return
        with self._tracer.start_as_current_span(name) as span:  # pragma: no cover
            for key, value in attributes.items():
                span.set_attribute(key, str(value))
            yield


class EventLog:
    def __init__(self, store: Store, feature_id: str, tracer: Tracer | None = None):
        self.store = store
        self.feature_id = feature_id
        self.tracer = tracer

    def emit(self, type_: str, actor: str, summary: str, **payload: Any) -> None:
        log.info("[%s] %s | %s | %s", self.feature_id, type_, actor, summary)
        self.store.add_event(self.feature_id, type_, actor, summary, payload)

    def llm_observer(self, call: dict[str, Any]) -> None:
        self.emit(
            EVENT_AGENT,
            call["agent"],
            f"{call['agent']} -> {call['schema']} via {call['provider']} ({'ok' if call['ok'] else 'invalid'}, attempt {call['attempt']}, {call['duration_seconds']}s)",
            **call,
        )
