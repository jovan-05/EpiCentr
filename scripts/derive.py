"""Derive the computed parts of the graph (Block C), then print a report.

The derive itself (COLLABORATES_WITH + the Q3 comprehension score) lives in api/seed.py, because the API
re-runs it on boot and after incidents. This script runs it and prints: Q4 who_knows('payments-api'),
Q10 comprehension map, stamp chains, knowledge-spreading, Q14 what-if, G1 PageRank, G2 betweenness, G3 shadow teams.

"Now" is the demo clock in (:Dataset).as_of, never the wall clock.

Run:  .venv\\Scripts\\python.exe scripts\\derive.py [graph]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api import scoring  # noqa: E402
from api.db import graph as connect, rows_of  # noqa: E402
from api.seed import derive  # noqa: E402,F401  (re-exported for tests)

# ---------------------------------------------------------------- report queries

# Q4 who_knows (index-anchored form, see CLAUDE.md): active people ranked by score, with their recent PRs as evidence.
Q4 = """
MATCH (s:Service {name: $service})
WITH s
MATCH (s)<-[k:UNDERSTANDS]-(p:Person) WHERE p.status = 'active'
OPTIONAL MATCH (p)-[:AUTHORED|REVIEWED]->(pr:PR)-[:TOUCHES]->(s)
WITH p, k, s, pr ORDER BY pr.merged_at DESC
RETURN p.handle AS person, p.role AS role, k.score AS score,
       k.authored AS authored, k.deep_reviews AS deep_reviews, k.stamps AS stamps,
       collect(pr.id) AS recent_prs
ORDER BY score DESC
LIMIT $k
"""

# Q10 comprehension map: per service, agent share of recent lines and the active experts -> zone.
Q10 = """
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

# Stamp chains: agent-written PRs a human approved in under 2 minutes with no comments.
STAMP_CHAINS = """
MATCH (ag:CodingAgent)-[:AUTHORED]->(pr:PR)-[:TOUCHES]->(s:Service {name: $service})
MATCH (pr)<-[r:REVIEWED]-(p:Person)
WHERE r.secs < 120 AND r.comments = 0
RETURN pr.id AS pr, pr.lines AS lines, ag.name AS agent, p.handle AS approver, r.secs AS secs
ORDER BY pr.lines DESC LIMIT 5
"""

# Knowledge-spreading: mentor = top active scorer; learner = most shallow touches (stamps + agent prompts).
KNOWLEDGE_SPREAD = """
MATCH (e:Person {status: 'active'})-[ue:UNDERSTANDS]->(s:Service {name: $service})
MATCH (l:Person {status: 'active'})-[ul:UNDERSTANDS]->(s)
WHERE l <> e
RETURN e.handle AS mentor, ue.score AS mentor_score, l.handle AS learner, ul.stamps + ul.prompted AS shallow_touches
ORDER BY ue.score DESC, shallow_touches DESC LIMIT 1
"""

# Q14 what-if: services whose experts drop to 0 or 1 if $gone leave.
Q14 = """
MATCH (s:Service)
OPTIONAL MATCH (p:Person {status: 'active'})-[u:UNDERSTANDS]->(s)
WHERE u.score >= $threshold
WITH s, collect(p.handle) AS before
WITH s, before, [h IN before WHERE NOT h IN $gone] AS after
WHERE size(after) < size(before) AND size(after) <= 1
RETURN s.name AS service, s.tier AS tier, before, after,
       CASE WHEN size(after) = 0 THEN 'dark' ELSE 'fragile' END AS new_zone
ORDER BY tier, size(after)
"""

# Q14 learner: active people who stay AND already have evidence on the service, plus collaboration with the leavers.
Q14_LEARNER = """
MATCH (s:Service {name: $service})
WITH s
MATCH (l:Person {status: 'active'})-[u:UNDERSTANDS]->(s)
WHERE NOT l.handle IN $gone
OPTIONAL MATCH (l)-[c:COLLABORATES_WITH]->(g:Person) WHERE g.handle IN $gone
WITH l, u.score AS score, sum(c.shared) AS shared
WITH l, score + 0.5 * coalesce(shared, 0) AS readiness
RETURN l.handle AS learner, readiness
ORDER BY readiness DESC LIMIT 2
"""

# G1: PageRank over DEPENDS_ON; heavily depended-on services score highest.
G1 = """
CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score
RETURN node.name AS service, node.tier AS tier, score ORDER BY score DESC LIMIT 5
"""

# G2: betweenness on collaboration; people who sit on the paths between groups.
G2 = """
CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATES_WITH'], samplingSize: 64, samplingSeed: 42})
YIELD node, score
RETURN node.handle AS person, node.status AS status, score ORDER BY score DESC LIMIT 5
"""

# G3: label propagation communities vs official teams; a community spanning teams is a shadow team.
G3 = """
CALL algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATES_WITH'], maxIterations: 20})
YIELD node, communityId
MATCH (node)-[m:MEMBER_OF]->(t:Team) WHERE m.valid_to IS NULL
RETURN communityId, collect(node.handle) AS people, collect(DISTINCT t.slug) AS official_teams, count(*) AS size
ORDER BY size DESC
"""


rows = rows_of


def fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}" if abs(v) >= 0.1 or v == 0 else f"{v:.5f}"   # PageRank scores are small
    return str(v)


def table(title, data, cols):
    print(f"\n{title}")
    widths = [max(len(c), *(len(fmt(r[c])) for r in data)) if data else len(c) for c in cols]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in data:
        print("  " + "  ".join(fmt(r[c]).ljust(w) for c, w in zip(cols, widths)))


def report(g, now):
    p = {"now": now, "threshold": scoring.THRESHOLD}
    q4 = rows(g.ro_query(Q4, {"service": "payments-api", "k": 6}))
    for r in q4:
        r["recent_prs"] = ", ".join(r["recent_prs"][:3])   # trim to 3 in Python (04)
    table("Q4 who_knows('payments-api')", q4, ["person", "role", "score", "authored", "deep_reviews", "stamps", "recent_prs"])
    table(f"Q10 comprehension map (threshold {scoring.THRESHOLD})", rows(g.ro_query(Q10, p)),
          ["service", "tier", "zone", "human_experts", "agent_share", "experts"])
    table("Stamp chains on ledger", rows(g.ro_query(STAMP_CHAINS, {"service": "ledger"})), ["pr", "lines", "agent", "approver", "secs"])
    table("Knowledge-spreading for ledger", rows(g.ro_query(KNOWLEDGE_SPREAD, {"service": "ledger"})),
          ["mentor", "mentor_score", "learner", "shallow_touches"])
    table("Q14 what if marco leaves?", rows(g.ro_query(Q14, {**p, "gone": ["marco"]})), ["service", "tier", "before", "after", "new_zone"])
    table("Q14 learner for payments-api", rows(g.ro_query(Q14_LEARNER, {"service": "payments-api", "gone": ["marco"]})), ["learner", "readiness"])
    table("G1 PageRank (critical services)", rows(g.ro_query(G1)), ["service", "tier", "score"])
    table("G2 betweenness (bridge people)", rows(g.ro_query(G2)), ["person", "status", "score"])
    g3 = [r for r in rows(g.ro_query(G3)) if len(r["official_teams"]) >= 3]
    table("G3 communities spanning 3+ official teams (shadow teams)", g3, ["communityId", "size", "official_teams", "people"])


def main():
    g = connect(sys.argv[1] if len(sys.argv) > 1 else None)
    t = time.time()
    now, n_ev, n_u = derive(g)
    n_c = g.ro_query("MATCH ()-[c:COLLABORATES_WITH]->() RETURN count(c)").result_set[0][0]
    print(f"Derived in {time.time() - t:.1f}s: {n_ev} evidence rows -> {n_u} UNDERSTANDS edges; {n_c} COLLABORATES_WITH edges")
    report(g, now)


if __name__ == "__main__":
    main()
