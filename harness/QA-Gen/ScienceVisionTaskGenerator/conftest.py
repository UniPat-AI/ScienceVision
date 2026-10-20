# conftest.py — presence puts this directory on sys.path so that
# absolute imports like `from sciencevision_taskgen...` resolve correctly.
# Also provides shared pytest fixtures.

import json
import pathlib
import pytest

_SCHEMA_PATH = (
    pathlib.Path(__file__).parent / "schemas" / "sciencevision_task.schema.json"
)


@pytest.fixture(scope="session")
def schema() -> dict:
    with _SCHEMA_PATH.open() as f:
        return json.load(f)
