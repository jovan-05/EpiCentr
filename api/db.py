"""FalkorDB access shared by every tool.

Every tool returns the same shape (CLAUDE.md):
    {rows, keys, cypher, params, ms}
- rows:   list of dicts, one per result row
- keys:   stable node keys the answer used, e.g. "Person:marco", in the order they were found
          (the UI highlights them; the grounding guard checks claims against them)
- cypher: the exact statement(s) that ran, for the "show me the query" panel
- params: the $params they ran with
- ms:     FalkorDB's own execution time, summed over the statements
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from falkordb import FalkorDB

ROOT = Path(__file__).resolve().parent.parent

# Key property per label, e.g. Person -> handle (written by generate_data.py).
KEYS = json.loads((ROOT / "data" / "northwind" / "meta.json").read_text(encoding="utf-8"))["keys"]

# The demo clock, written by scripts/load.py. "Now" for every query.
CLOCK = "MATCH (d:Dataset) RETURN d.as_of AS now, d.week AS week"


@lru_cache(maxsize=1)
def _client():
    return FalkorDB(host=os.getenv("FALKORDB_HOST", "localhost"), port=int(os.getenv("FALKORDB_PORT", "6379")))


def graph(name=None):
    return _client().select_graph(name or os.getenv("FALKORDB_GRAPH", "northwind"))


def rows_of(res):
    cols = [h[1] for h in res.header]
    return [dict(zip(cols, r)) for r in res.result_set]


def clock(g):
    """(now_ms, iso_week) from the (:Dataset) node."""
    r = rows_of(g.ro_query(CLOCK))[0]
    return r["now"], r["week"]


class Trace:
    """Runs statements for one tool call and assembles the standard result."""

    def __init__(self, g):
        self.g = g
        self.cypher, self.params, self.keys, self.ms = [], {}, [], 0.0

    def _record(self, cypher, params, res):
        self.cypher.append(cypher.strip())
        self.params.update(params or {})
        self.ms += float(res.run_time_ms or 0)

    def read(self, cypher, params=None):
        res = self.g.ro_query(cypher, params or {})   # read-only: FalkorDB refuses any write here
        self._record(cypher, params, res)
        return rows_of(res)

    def write(self, cypher, params=None):
        res = self.g.query(cypher, params or {})
        self._record(cypher, params, res)
        return rows_of(res)

    def key(self, label, value):
        """Remember a node the answer depends on. Ignores None (OPTIONAL MATCH misses)."""
        if value is None:
            return
        assert label in KEYS, label
        k = f"{label}:{value}"
        if k not in self.keys:
            self.keys.append(k)

    def result(self, rows, **extra):
        out = {"rows": rows, "keys": self.keys, "cypher": "\n\n".join(self.cypher), "params": self.params,
               "ms": round(self.ms, 3)}
        out.update(extra)
        return out
