from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The factories module is imported by name from every forensic test.
sys.path.insert(0, str(Path(__file__).parent))

from factories import (  # noqa: E402
    healthy_trace,
    model_failure_trace,
    postprocessing_failure_trace,
    retrieval_failure_trace,
    tool_timeout_trace,
)


@pytest.fixture
def healthy():
    return healthy_trace()


@pytest.fixture
def retrieval_failure():
    return retrieval_failure_trace()


@pytest.fixture
def model_failure():
    return model_failure_trace()


@pytest.fixture
def postprocessing_failure():
    return postprocessing_failure_trace()


@pytest.fixture
def tool_timeout():
    return tool_timeout_trace()
