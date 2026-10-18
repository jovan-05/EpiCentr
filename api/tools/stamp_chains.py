"""stamp_chains (Q10 evidence): agent-written code a human approved without reading."""

from api.db import Trace, graph

# Agent-authored PRs on the service that a human approved in under 2 minutes with no comments, biggest first.
# Graph: agent -> PR -> service and reviewer -> PR, filtered on review behaviour stored on the edge.
CYPHER = """
MATCH (s:Service {name: $service})
WITH s
MATCH (ag:CodingAgent)-[:AUTHORED]->(pr:PR)-[:TOUCHES]->(s)
MATCH (pr)<-[r:REVIEWED]-(p:Person)
WHERE r.secs < 120 AND r.comments = 0
OPTIONAL MATCH (pp:Person)-[:PROMPTED]->(pr)
RETURN pr.id AS pr, pr.title AS title, pr.lines AS lines, ag.name AS agent,
       pp.handle AS prompted_by, p.handle AS approver, r.secs AS secs
ORDER BY lines DESC LIMIT 10
"""


# Totals for the headline ("16 agent PRs approved in under 90 s"): all stamped agent PRs, not just the top 10.
SUMMARY = """
MATCH (s:Service {name: $service})
WITH s
MATCH (ag:CodingAgent)-[:AUTHORED]->(pr:PR)-[:TOUCHES]->(s)
MATCH (pr)<-[r:REVIEWED]-(p:Person)
WHERE r.secs < 120 AND r.comments = 0
RETURN count(DISTINCT pr) AS stamped_prs, sum(CASE WHEN r.secs < 90 THEN 1 ELSE 0 END) AS under_90s,
       avg(r.secs) AS avg_secs
"""


def stamp_chains(service: str, g=None) -> dict:
    """Rubber-stamped agent PRs on `service`: pr, title, lines, agent, prompted_by, approver, secs.
    Also returns `summary` {stamped_prs, under_90s, avg_secs} over all of them."""
    t = Trace(g or graph())
    summary = t.read(SUMMARY, {"service": service})[0]
    rows = t.read(CYPHER, {"service": service})
    t.key("Service", service)
    for r in rows:
        t.key("PR", r["pr"])
        t.key("CodingAgent", r["agent"])
        t.key("Person", r["prompted_by"])
        t.key("Person", r["approver"])
    return t.result(rows, summary=summary)
