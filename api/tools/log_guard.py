"""log_guard (A6): audit every decision-guard call in the graph."""

import uuid

from api.db import Trace, graph

# Record the guard call. `at` is the wall clock: this is a real audit event, not story data.
CREATE = """
CREATE (g:GuardCheck {id: $id, service: $service, proposal: $proposal, verdict: $verdict, caller: $caller, at: timestamp()})
RETURN g.id AS id
"""

# Link the call to the decision it matched (only run when one matched).
MATCHED = """
MATCH (g:GuardCheck {id: $id}), (d:Decision {id: $decision_id})
CREATE (g)-[:MATCHED]->(d)
"""


def log_guard(service: str, proposal: str, verdict: str, caller: str, decision_id: str = None, g=None) -> dict:
    """Write a GuardCheck node (and MATCHED -> Decision if one matched). Returns its id in rows[0]."""
    t = Trace(g or graph())
    gid = f"GC-{uuid.uuid4().hex[:10]}"
    rows = t.write(CREATE, {"id": gid, "service": service, "proposal": proposal, "verdict": verdict, "caller": caller})
    if decision_id:
        t.write(MATCHED, {"id": gid, "decision_id": decision_id})
        t.key("Decision", decision_id)
    return t.result(rows)
