"""knowledge_spread (Q10 suggestion): who should teach whom on this service."""

from api.db import Trace, graph

# Mentor = the active person who understands the service best. Learner = another active person who has
# touched it most without understanding it (rubber stamps + agent PRs they prompted).
# Graph: both come from UNDERSTANDS edges on the same service, compared in one query.
CYPHER = """
MATCH (s:Service {name: $service})
WITH s
MATCH (e:Person {status: 'active'})-[ue:UNDERSTANDS]->(s)
MATCH (l:Person {status: 'active'})-[ul:UNDERSTANDS]->(s)
WHERE l <> e
RETURN e.handle AS mentor, ue.score AS mentor_score,
       l.handle AS learner, ul.score AS learner_score, ul.stamps + ul.prompted AS shallow_touches
ORDER BY mentor_score DESC, shallow_touches DESC LIMIT 1
"""


def knowledge_spread(service: str, g=None) -> dict:
    """One pairing suggestion for `service`: mentor, mentor_score, learner, learner_score, shallow_touches."""
    t = Trace(g or graph())
    rows = t.read(CYPHER, {"service": service})
    t.key("Service", service)
    for r in rows:
        t.key("Person", r["mentor"])
        t.key("Person", r["learner"])
    return t.result(rows)
