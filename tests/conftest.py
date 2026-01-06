from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

import pytest


def _safe_node_name(name: str) -> str:
    # Windows-safe-ish: keep alnum, dash, underscore, dot.
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "test"


@pytest.fixture
def workspace_tmp_path(request: pytest.FixtureRequest) -> Path:
    """Temp dir rooted in the workspace (not system temp).

    This repo's environment can deny access to dirs created under the system temp
    directory; using a workspace-local temp dir avoids that.
    """
    root = Path.cwd() / ".pytest_tmp_workspace" / _safe_node_name(request.node.name) / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)

