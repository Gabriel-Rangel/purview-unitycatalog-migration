import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIXTURES = os.path.join(ROOT, "tests", "fixtures")

from purview2uc.config import load_config  # noqa: E402


@pytest.fixture
def cfg():
    return load_config(os.path.join(ROOT, "config", "config.yaml"))


@pytest.fixture
def assets():
    """Snapshot normalizado extraído do Purview de teste (demo/seed_purview.py)."""
    with open(os.path.join(FIXTURES, "assets_snapshot.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def raw_entities():
    """Respostas cruas da Atlas API (entidade de tabela + entidades de coluna)."""
    with open(os.path.join(FIXTURES, "purview_entities.json"), encoding="utf-8") as f:
        return json.load(f)
