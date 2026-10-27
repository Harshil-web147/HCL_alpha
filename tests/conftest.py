"""Shared fixtures for the platform's own test-suite.

All tests run with ``LLM_PROVIDER=stub`` (no network, no keys) against a
temporary database and workspace root.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="sdlc-tests-"))
os.environ["LLM_PROVIDER"] = "stub"
os.environ["SDLC_DATA_DIR"] = str(_TMP / "data")
os.environ["SDLC_WORKSPACE_ROOT"] = str(_TMP / "workspaces")
os.environ["SDLC_ENABLE_PIP_AUDIT"] = "false"
os.environ["SDLC_AUTO_LAUNCH_PRODUCT"] = "false"
os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

from app.config import get_settings, reset_settings  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402
from app.llm.stub import StubProvider  # noqa: E402
from app.models import FeatureRequest  # noqa: E402
from app.orchestration.engine import WorkflowEngine  # noqa: E402
from app.orchestration.gates import GateConfig  # noqa: E402
from app.persistence import Store  # noqa: E402

reset_settings()

EXAMPLE_REQUEST = FeatureRequest(
    title="Customer contact update API",
    description=(
        "Develop an API allowing customers to update their contact information. Validate mandatory "
        "fields, prevent unauthorized modification, maintain an audit record and return appropriate "
        "API error responses."
    ),
)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def gate_config(settings):
    return GateConfig.load(settings.gates_config)


@pytest.fixture
def engine(store, settings, gate_config):
    return WorkflowEngine(store, settings, provider=StubProvider(), gate_config=gate_config)


@pytest.fixture
def llm(settings):
    return LLMClient(settings, provider=StubProvider())


@pytest.fixture
def example_request():
    return EXAMPLE_REQUEST.model_copy()
