"""cite (A2): store the proof behind each claim as CITES edges from the incident."""

from api.db import KEYS, Trace, graph


def _cypher(label):
    # Link the incident to every node a claim relies on, keeping the claim text and its full key path.
    # Labels can't be $params; `label` comes from the fixed KEYS whitelist (checked below), values are $params.
    assert label in KEYS
    return (f"MATCH (i:Incident {{id: $id}}) UNWIND $rows AS c "
            f"MATCH (n:{label} {{{KEYS[label]}: c.key}}) "
            f"CREATE (i)-[:CITES {{claim: c.claim, path_keys: c.path_keys}}]->(n)")


def cite(incident_id: str, citations: list, g=None) -> dict:
    """citations: [{"claim": "...", "keys": ["Person:marco", "PR:PR-4452"]}].
    Creates (Incident)-[:CITES {claim, path_keys}]->(node) for every key. Stable keys only, never id(n)."""
    t = Trace(g or graph())
    by_label = {}
    for c in citations:
        for k in c["keys"]:
            label, _, value = k.partition(":")
            if label not in KEYS:
                raise ValueError(f"unknown label in key {k!r}")
            by_label.setdefault(label, []).append({"key": value, "claim": c["claim"], "path_keys": c["keys"]})
    for label, rows in sorted(by_label.items()):
        t.write(_cypher(label), {"id": incident_id, "rows": rows})
        for r in rows:
            t.key(label, r["key"])
    t.key("Incident", incident_id)
    return t.result([{"incident": incident_id, "cited": sum(len(r) for r in by_label.values())}])
