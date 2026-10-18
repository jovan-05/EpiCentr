"""Shared fixtures: fresh copies of the Northwind graph in FalkorDB, loaded + derived from data/northwind.

- `g`  (session): read-only tests share one graph, `kt_read`.
- `wg` (module):  tests that write get their own graph, `kt_write_<module>`, so they can't disturb reads.
Skipped when FalkorDB isn't running (docker compose up -d).
"""

import pytest

from api import seed
from api.db import graph as connect


def _fresh(name):
    graph = connect(name)
    try:
        graph.execute_command("PING")
    except Exception as ex:  # noqa: BLE001
        pytest.skip(f"FalkorDB not reachable: {ex}")
    seed.load(graph)
    graph.now, _, _ = seed.derive(graph)
    return graph


@pytest.fixture(scope="session")
def g():
    graph = _fresh("kt_read")
    yield graph
    graph.delete()


@pytest.fixture(scope="module")
def wg(request):
    graph = _fresh(f"kt_write_{request.module.__name__.split('.')[-1]}")
    yield graph
    graph.delete()
