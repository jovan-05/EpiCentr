"""comprehension_map (Q10): dark zones."""

from api import scoring
from api.db import Trace, clock, graph

# Per service: share of the last 90 days' changed lines written by coding agents, and the active humans
# at or above the expert threshold -> zone dark / fragile / healthy. Graph: PR authorship (agent vs human)
# and derived understanding meet on the Service node.
CYPHER = """
MATCH (s:Service)
OPTIONAL MATCH (s)<-[:TOUCHES]-(pr:PR) WHERE pr.merged_at > $now - 7776000000
OPTIONAL MATCH (pr)<-[:AUTHORED]-(ag:CodingAgent)
WITH s, sum(pr.lines) AS lines,
     sum(CASE WHEN ag IS NULL THEN 0 ELSE pr.lines END) AS agent_lines
OPTIONAL MATCH (p:Person {status: 'active'})-[u:UNDERSTANDS]->(s) WHERE u.score >= $threshold
WITH s, lines, agent_lines, collect(p.handle) AS experts
RETURN s.name AS service, s.tier AS tier,
       CASE WHEN lines = 0 THEN 0.0 ELSE toFloat(agent_lines) / lines END AS agent_share,
       size(experts) AS human_experts, experts,
       CASE WHEN size(experts) = 0 THEN 'dark'
            WHEN size(experts) = 1 THEN 'fragile'
            ELSE 'healthy' END AS zone
ORDER BY tier ASC, human_experts ASC, agent_share DESC
"""


def comprehension_map(threshold: float = scoring.THRESHOLD, g=None) -> dict:
    """Every service with zone, agent_share (0-1) and its active experts. Critical dark zones come first."""
    g = g or graph()
    now, _ = clock(g)
    t = Trace(g)
    rows = t.read(CYPHER, {"now": now, "threshold": threshold})
    for r in rows:
        t.key("Service", r["service"])
        for h in r["experts"]:
            t.key("Person", h)
    return t.result(rows)
