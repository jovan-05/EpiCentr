"""edges_between: the real relationships between a set of nodes, so the 3D view draws only lines that exist.

The UI calls it when an agent step (or a claim) brings in evidence the map doesn't show (PRs, messages, docs,
tickets). Earlier the UI guessed those lines from the order of a tool's keys, which drew PR -> PR lines and
PRs linked to the wrong person. Now every evidence line is an edge read from FalkorDB, with its type and direction.
"""

from api.db import KEYS, Trace, graph

MAX_KEYS = 400   # a step or a claim cites tens of nodes; this only stops an accidental huge request

# Every node's stable key ("Label:value"), computed in Cypher with the same key property per label as KEYS
# (Person.handle, Team.slug, Ticket.key, Service/CodingAgent/Datastore.name, everything else .id).
_KEY_A = ("labels(a)[0] + ':' + CASE labels(a)[0] WHEN 'Person' THEN a.handle WHEN 'Team' THEN a.slug "
          "WHEN 'Ticket' THEN a.key WHEN 'Service' THEN a.name WHEN 'CodingAgent' THEN a.name "
          "WHEN 'Datastore' THEN a.name ELSE a.id END")
_KEY_B = _KEY_A.replace("(a)", "(b)").replace("a.", "b.")

# Edges from each focus node to any node in $among, with their real direction. Graph: "how is this PR connected
# to these people and this service" is a 1-hop pattern; a document store would have to guess from co-occurrence.
# The focus nodes have different labels, so they're found by key over all nodes (~1.4k, a few ms) instead of a
# per-label index; labels can't be query parameters, and building them into the text isn't worth it here.
CYPHER = f"""
MATCH (a)
WITH a, {_KEY_A} AS ka
WHERE ka IN $focus
MATCH (a)-[r]-(b)
WITH ka, r, startNode(r) = a AS out, {_KEY_B} AS kb
WHERE kb IN $among
RETURN DISTINCT CASE WHEN out THEN ka ELSE kb END AS source, type(r) AS type,
       CASE WHEN out THEN kb ELSE ka END AS target
"""


def edges_between(keys: list, focus: list = None, g=None) -> dict:
    """Real edges between `focus` nodes and any of `keys` (focus defaults to all of `keys`).
    Rows: {source, target, types}: one row per directed node pair, every relationship type between them
    (sam REVIEWED and PROMPTED the same PR -> one line, two types). `keys` = the focus nodes that exist."""
    keys = [k for k in dict.fromkeys(keys or []) if isinstance(k, str) and ":" in k][:MAX_KEYS]
    focus = [k for k in dict.fromkeys(focus if focus is not None else keys) if isinstance(k, str) and ":" in k][:MAX_KEYS]
    g = g or graph()
    t = Trace(g)
    among = list(dict.fromkeys(keys + focus))
    raw = t.read(CYPHER, {"focus": focus, "among": among}) if focus else []
    pairs = {}
    for r in raw:
        if r["source"] == r["target"]:
            continue
        pairs.setdefault((r["source"], r["target"]), []).append(r["type"])
    rows = [{"source": s, "target": d, "types": sorted(set(ts))} for (s, d), ts in sorted(pairs.items())]
    found = {k for row in rows for k in (row["source"], row["target"])}
    for k in focus:
        label, _, value = k.partition(":")
        if k in found and label in KEYS:
            t.key(label, value)
    return t.result(rows)
