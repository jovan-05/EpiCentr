"""Load data/northwind into FalkorDB and derive the computed edges. Used by the API (reseed on boot, /reset,
re-derive after an incident is resolved) and by scripts/load.py + scripts/derive.py.

load(g):   drops the graph, creates indexes (03), loads nodes (UNWIND 500, MERGE on key) and edges
           (UNWIND 500, CREATE: some edges are legitimately parallel, e.g. ON_CALL_FOR per week), and writes
           (:Dataset {as_of, week}): the demo clock, "now" for every query.
derive(g): COLLABORATES_WITH, then UNDERSTANDS (Q3: evidence by Cypher, weights in Python via api/scoring.py).

Labels / rel types can't be Cypher $params, so load() builds them into query text from the fixed KEYS
whitelist (regex-checked). All values are $params.
"""

import json
import re
from pathlib import Path

from api import scoring
from api.db import CLOCK, rows_of

DATA = Path(__file__).resolve().parent.parent / "data" / "northwind"
BATCH = 500
NAME = re.compile(r"^[A-Z][A-Za-z_]*$")
PROP = re.compile(r"^[a-z_]+$")

# Full-text indexes (03 + Ticket for prior_warnings).
FULLTEXT = [
    "CREATE FULLTEXT INDEX FOR (d:Doc) ON (d.title, d.body)",          # docs search
    "CREATE FULLTEXT INDEX FOR (m:Message) ON (m.text)",                # Q7 prior_warnings
    "CREATE FULLTEXT INDEX FOR (o:Option) ON (o.text, o.keywords)",     # Q11 decision guard
    "CREATE FULLTEXT INDEX FOR (t:Ticket) ON (t.title)",                # prior_warnings: open tickets (Story 3)
]

# Remove derived edges so a re-run starts clean.
CLEAR_COLLAB = "MATCH ()-[c:COLLABORATES_WITH]->() DELETE c"
CLEAR_UNDERSTANDS = "MATCH ()-[u:UNDERSTANDS]->() DELETE u"

# Pairs of people who worked on the same PR, in both directions. Graph: a 2-hop pattern join over PRs.
DERIVE_COLLAB = """
MATCH (a:Person)-[:AUTHORED|REVIEWED]->(pr:PR)<-[:AUTHORED|REVIEWED]-(b:Person)
WHERE id(a) < id(b)
WITH a, b, count(DISTINCT pr) AS shared
MERGE (a)-[r1:COLLABORATES_WITH]->(b) SET r1.shared = shared, r1.weight = 1.0 / shared
MERGE (b)-[r2:COLLABORATES_WITH]->(a) SET r2.shared = shared, r2.weight = 1.0 / shared
"""

# Q3a: a human authored a PR touching the service.
Q3A = """
MATCH (p:Person)-[a:AUTHORED]->(pr:PR)-[:TOUCHES]->(s:Service)
RETURN p.handle AS who, s.name AS svc, 'authored' AS kind, a.at AS at
"""

# Q3b: reviews, classified deep / stamp / light from how long and how much the reviewer engaged.
Q3B = """
MATCH (p:Person)-[r:REVIEWED]->(pr:PR)-[:TOUCHES]->(s:Service)
RETURN p.handle AS who, s.name AS svc,
       CASE WHEN r.secs >= 300 OR r.comments >= 2 THEN 'deep'
            WHEN r.secs < 120 AND r.comments = 0 THEN 'stamp'
            ELSE 'light' END AS kind,
       r.at AS at
"""

# Q3c: resolved incidents rooted in the service, credited to the person who handled them.
Q3C = """
MATCH (p:Person)<-[:ASSIGNED_TO]-(i:Incident {status: 'resolved'})-[:IMPACTS {role: 'root'}]->(s:Service)
RETURN p.handle AS who, s.name AS svc, 'incident' AS kind, i.opened_at AS at
"""

# Q3d: a human prompted a coding agent's PR; it only counts as understanding if they commented on it.
Q3D = """
MATCH (p:Person)-[pp:PROMPTED]->(pr:PR)-[:TOUCHES]->(s:Service)
RETURN p.handle AS who, s.name AS svc,
       CASE WHEN pp.comments > 0 THEN 'prompted' ELSE 'prompted_silent' END AS kind,
       pp.at AS at
"""

# Q3e: chat messages that mention the service.
Q3E = """
MATCH (p:Person)-[:SENT]->(m:Message)-[:MENTIONS]->(s:Service)
RETURN p.handle AS who, s.name AS svc, 'chat' AS kind, m.sent_at AS at
"""

# Q3w: write the scores as UNDERSTANDS edges (one hop at question time).
Q3W = """
UNWIND $rows AS r
MATCH (p:Person {handle: r.who}), (s:Service {name: r.svc})
MERGE (p)-[u:UNDERSTANDS]->(s)
SET u.score = r.score, u.authored = r.authored, u.deep_reviews = r.deep_reviews, u.light_reviews = r.light_reviews,
    u.stamps = r.stamps, u.prompted = r.prompted, u.last_seen = r.last_seen, u.computed_at = $now
"""


def _safe(name, pattern=NAME):
    assert pattern.match(name), f"refusing to put {name!r} into query text"
    return name


def _batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


def load(g, data_dir=DATA):
    """Rebuild graph `g` from data_dir. Returns (meta, counts)."""
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    keys = meta["keys"]
    try:
        g.delete()
    except Exception:  # noqa: BLE001
        pass  # graph did not exist

    for label, key in keys.items():
        g.query(f"CREATE INDEX FOR (n:{_safe(label)}) ON (n.{_safe(key, PROP)})")
    for q in FULLTEXT:
        g.query(q)

    counts = {}
    for path in sorted((data_dir / "nodes").glob("*.json")):
        label = _safe(path.stem)
        key = _safe(keys[label], PROP)
        rows = json.loads(path.read_text(encoding="utf-8"))
        # Upsert each node by its stable key and copy all properties.
        q = f"UNWIND $rows AS r MERGE (n:{label} {{{key}: r.{key}}}) SET n += r"
        for b in _batches(rows):
            g.query(q, {"rows": b})
        counts[label] = len(rows)

    for path in sorted((data_dir / "edges").glob("*.json")):
        rtype = _safe(path.stem)
        rows = json.loads(path.read_text(encoding="utf-8"))
        groups = {}
        for r in rows:   # one query per (src label, dst label), e.g. AUTHORED from Person and from CodingAgent
            props = {k: v for k, v in r.items() if k not in ("src_label", "src", "dst_label", "dst")}
            groups.setdefault((r["src_label"], r["dst_label"]), []).append({"src": r["src"], "dst": r["dst"], "props": props})
        for (sl, dl), grp in sorted(groups.items()):
            # Find both endpoints by key (index lookups) and create the relationship with its properties.
            q = (f"UNWIND $rows AS r MATCH (a:{_safe(sl)} {{{_safe(keys[sl], PROP)}: r.src}}) "
                 f"MATCH (b:{_safe(dl)} {{{_safe(keys[dl], PROP)}: r.dst}}) "
                 f"CREATE (a)-[e:{rtype}]->(b) SET e += r.props")
            for b in _batches(grp):
                g.query(q, {"rows": b})
        counts[rtype] = len(rows)

    # The demo clock: every query reads $now / $week from here.
    g.query("MERGE (d:Dataset {name: $name}) SET d.as_of = $as_of, d.week = $week, d.loaded_at = timestamp()",
            {"name": meta["company"], "as_of": meta["as_of_ms"], "week": meta["as_of_week"]})
    return meta, counts


def derive(g):
    """Recompute COLLABORATES_WITH and UNDERSTANDS. Returns (now, evidence_rows, understands_edges)."""
    now = rows_of(g.ro_query(CLOCK))[0]["now"]
    g.query(CLEAR_COLLAB)
    g.query(DERIVE_COLLAB)
    evidence = []
    for q in (Q3A, Q3B, Q3C, Q3D, Q3E):
        evidence += [(r["who"], r["svc"], r["kind"], r["at"]) for r in rows_of(g.ro_query(q))]
    scores = scoring.score(evidence, now)
    payload = [{"who": w, "svc": s, **u} for (w, s), u in sorted(scores.items())]
    g.query(CLEAR_UNDERSTANDS)
    for b in _batches(payload):
        g.query(Q3W, {"rows": b, "now": now})
    return now, len(evidence), len(payload)


def is_empty(g):
    try:
        return g.ro_query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
    except Exception:  # noqa: BLE001  (graph key doesn't exist yet)
        return True


def reseed(g):
    """load + derive. Returns a short summary."""
    meta, counts = load(g)
    now, n_ev, n_u = derive(g)
    return {"graph": g.name, "as_of": meta["as_of_iso"], "nodes": sum(meta["counts"]["nodes"].values()) + 1,
            "edges": sum(meta["counts"]["edges"].values()), "understands": n_u}
