"""Day-1 Cypher verification (Block A).

Builds a small toy graph in FalkorDB and runs every item from the
"Day-1 verification list" in docs/plan/04-cypher-queries.md, plus a few
extra checks for syntax the later blocks depend on (graph algorithms,
map collects, string concat in MERGE). Prints PASS/FAIL per item.

The toy graph is kept in the graph `verify_toy`, so you can open the
FalkorDB Browser (http://localhost:3000) afterwards and look at it:
    MATCH (n)-[r]->(m) RETURN n, r, m

Run:  .venv\\Scripts\\python.exe scripts\\verify_cypher.py
"""

import os
import sys
import traceback

from falkordb import FalkorDB

HOST = os.getenv("FALKORDB_HOST", "localhost")
PORT = int(os.getenv("FALKORDB_PORT", "6379"))
GRAPH = "verify_toy"

DAY = 86_400_000
NOW = 1_792_000_000_000  # fixed epoch ms so results are reproducible

# Toy graph: 5 services, 4 people, 1 team, 1 coding agent, 3 PRs, plus one
# decision/option/incident and one message/thread, which are needed to
# exercise the full-text queries. All names are synthetic.
SEED = [
    # services and the dependency chain web -> checkout -> payments-api -> {ledger, fraud-svc}
    """CREATE (:Service {name:'web', tier:1}), (:Service {name:'checkout', tier:1}),
              (:Service {name:'payments-api', tier:1}), (:Service {name:'ledger', tier:1}),
              (:Service {name:'fraud-svc', tier:2})""",
    """MATCH (w:Service {name:'web'}), (c:Service {name:'checkout'}), (p:Service {name:'payments-api'}),
             (l:Service {name:'ledger'}), (f:Service {name:'fraud-svc'})
       CREATE (w)-[:DEPENDS_ON]->(c), (c)-[:DEPENDS_ON]->(p),
              (p)-[:DEPENDS_ON]->(l), (p)-[:DEPENDS_ON]->(f)""",
    # people, team, agent
    """CREATE (:Person {handle:'marco', status:'active', role:'engineer'}),
              (:Person {handle:'sam', status:'active', role:'engineer'}),
              (:Person {handle:'priya', status:'departed', role:'engineer', left_at: $now - 60 * $day}),
              (:Person {handle:'lena', status:'active', role:'sre'}),
              (:Team {slug:'payments'}), (:CodingAgent {name:'codegen-bot'})""",
    """MATCH (t:Team {slug:'payments'}), (p:Service {name:'payments-api'}), (c:Service {name:'checkout'})
       CREATE (t)-[:OWNS {valid_from: 0}]->(p), (t)-[:OWNS {valid_from: 0}]->(c)""",
    """MATCH (t:Team {slug:'payments'}), (m:Person {handle:'marco'}), (s:Person {handle:'sam'}),
             (pr:Person {handle:'priya'}), (l:Person {handle:'lena'})
       CREATE (m)-[:MEMBER_OF {valid_from: 0}]->(t), (s)-[:MEMBER_OF {valid_from: 0}]->(t),
              (pr)-[:MEMBER_OF {valid_from: 0, valid_to: $now - 60 * $day}]->(t),
              (l)-[:REPORTS_TO]->(m)""",
    # PRs: PR-1 human (marco, deep review by sam), PR-2 agent (stamped by sam), PR-3 ledger (priya, reviewed by marco)
    """MATCH (pa:Service {name:'payments-api'}), (le:Service {name:'ledger'}),
             (m:Person {handle:'marco'}), (s:Person {handle:'sam'}), (pr:Person {handle:'priya'}),
             (bot:CodingAgent {name:'codegen-bot'})
       CREATE (p1:PR {id:'PR-1', title:'Add retries', lines:100, merged_at: $now - 10 * $day}),
              (p2:PR {id:'PR-2', title:'Remove fraud timeout', lines:300, merged_at: $now - 1 * $day}),
              (p3:PR {id:'PR-3', title:'Ledger dedupe', lines:50, merged_at: $now - 30 * $day}),
              (p1)-[:TOUCHES]->(pa), (p2)-[:TOUCHES]->(pa), (p3)-[:TOUCHES]->(le),
              (m)-[:AUTHORED {at: $now - 10 * $day}]->(p1),
              (s)-[:REVIEWED {secs: 600, comments: 3, at: $now - 10 * $day}]->(p1),
              (s)-[:REVIEWED {secs: 200, comments: 1, at: $now - 9 * $day}]->(p1),
              (bot)-[:AUTHORED {at: $now - 1 * $day}]->(p2),
              (s)-[:REVIEWED {secs: 38, comments: 0, at: $now - 1 * $day}]->(p2),
              (pr)-[:AUTHORED {at: $now - 30 * $day}]->(p3),
              (m)-[:REVIEWED {secs: 400, comments: 2, at: $now - 30 * $day}]->(p3)""",
    # materialised UNDERSTANDS (normally computed by derive.py)
    """MATCH (m:Person {handle:'marco'}), (s:Person {handle:'sam'}), (pr:Person {handle:'priya'}),
             (pa:Service {name:'payments-api'}), (le:Service {name:'ledger'})
       CREATE (m)-[:UNDERSTANDS {score: 8.0, authored: 1, deep_reviews: 0, stamps: 0}]->(pa),
              (s)-[:UNDERSTANDS {score: 0.4, authored: 0, deep_reviews: 1, stamps: 1}]->(pa),
              (pr)-[:UNDERSTANDS {score: 6.0, authored: 1, deep_reviews: 0, stamps: 0}]->(le),
              (m)-[:UNDERSTANDS {score: 2.5, authored: 0, deep_reviews: 1, stamps: 0}]->(le)""",
    # decision guard material: ADR-009 rejected removing idempotency on ledger after INC-007
    """MATCH (le:Service {name:'ledger'}), (l:Person {handle:'lena'})
       CREATE (d:Decision {id:'ADR-009', summary:'Keep idempotency keys in ledger', status:'active', decided_at: $now - 200 * $day}),
              (o:Option {id:'ADR-009-rejected', text:'Remove the idempotency check and dedupe logic in ledger'}),
              (i:Incident {id:'INC-007', title:'Double payouts', status:'resolved', opened_at: $now - 210 * $day}),
              (d)-[:REJECTED {reason:'Caused double payouts'}]->(o), (d)-[:AFFECTS]->(le),
              (d)-[:CAUSED_BY]->(i), (l)-[:DECIDED]->(d)""",
    # a warning message from lena about payments-api
    """MATCH (pa:Service {name:'payments-api'}), (l:Person {handle:'lena'})
       CREATE (th:Thread {topic:'fraud latency', channel:'#payments'}),
              (msg:Message {text:'The fraud-svc call has no circuit breaker; a timeout there is a big risk', sent_at: $now - 200 * $day}),
              (l)-[:SENT]->(msg), (msg)-[:IN_THREAD]->(th), (msg)-[:MENTIONS]->(pa)""",
]

INDEXES = [
    "CREATE INDEX FOR (s:Service) ON (s.name)",
    "CREATE INDEX FOR (p:Person) ON (p.handle)",
    "CREATE FULLTEXT INDEX FOR (m:Message) ON (m.text)",
    "CREATE FULLTEXT INDEX FOR (o:Option) ON (o.text)",
]

# Derive COLLABORATES_WITH, exactly as in 04 (also checks MERGE after WITH and id(a) < id(b)).
DERIVE_COLLAB = """
MATCH (a:Person)-[:AUTHORED|REVIEWED]->(pr:PR)<-[:AUTHORED|REVIEWED]-(b:Person)
WHERE id(a) < id(b)
WITH a, b, count(DISTINCT pr) AS shared
MERGE (a)-[r1:COLLABORATES_WITH]->(b) SET r1.shared = shared, r1.weight = 1.0 / shared
MERGE (b)-[r2:COLLABORATES_WITH]->(a) SET r2.shared = shared, r2.weight = 1.0 / shared
"""

Q1 = """
MATCH p = (down:Service {name: $service})<-[:DEPENDS_ON*1..4]-(up:Service)
WITH up, min(length(p)) AS hops, collect(p)[0] AS sample
OPTIONAL MATCH (up)<-[o:OWNS]-(t:Team) WHERE o.valid_to IS NULL
RETURN up.name AS service, up.tier AS tier, hops, t.slug AS owner_team,
       [n IN nodes(sample) | id(n)] AS path_node_ids
ORDER BY tier ASC, hops ASC
"""

Q4 = """
MATCH (p:Person {status: 'active'})-[k:UNDERSTANDS]->(s:Service {name: $service})
OPTIONAL MATCH (p)-[:AUTHORED|REVIEWED]->(pr:PR)-[:TOUCHES]->(s)
WITH p, k, s, pr ORDER BY pr.merged_at DESC
RETURN p.handle AS person, p.role AS role, k.score AS score,
       k.authored AS authored, k.deep_reviews AS deep_reviews, k.stamps AS stamps, k.last_seen AS last_seen,
       collect(pr.id) AS recent_prs,
       [id(p), id(s)] AS path_node_ids
ORDER BY score DESC
LIMIT $k
"""

# Fallback for Q4: the planner anchors Q4 on a Person label scan. Matching the
# indexed Service alone, then WITH, forces it to start from the Service(name) index.
Q4_FALLBACK = """
MATCH (s:Service {name: $service})
WITH s
MATCH (s)<-[k:UNDERSTANDS]-(p:Person) WHERE p.status = 'active'
OPTIONAL MATCH (p)-[:AUTHORED|REVIEWED]->(pr:PR)-[:TOUCHES]->(s)
WITH p, k, s, pr ORDER BY pr.merged_at DESC
RETURN p.handle AS person, p.role AS role, k.score AS score,
       k.authored AS authored, k.deep_reviews AS deep_reviews, k.stamps AS stamps, k.last_seen AS last_seen,
       collect(pr.id) AS recent_prs,
       [id(p), id(s)] AS path_node_ids
ORDER BY score DESC
LIMIT $k
"""

Q5 = """
MATCH (a:Person {handle: $from}), (b:Person {handle: $to})
CALL algo.SPpaths({
  sourceNode: a, targetNode: b,
  relTypes: ['COLLABORATES_WITH', 'REPORTS_TO'],
  weightProp: 'weight',
  relDirection: 'both',
  maxLen: 4,
  pathCount: 1
})
YIELD path, pathWeight
RETURN [n IN nodes(path) | n.handle] AS chain, pathWeight
"""

Q6 = """
MATCH (s:Service {name: $service})<-[:AFFECTS]-(d:Decision)
OPTIONAL MATCH (d)<-[:DECIDED]-(dec:Person)
OPTIONAL MATCH (newer:Decision)-[:SUPERSEDES]->(d)
RETURN d.id AS decision, collect(DISTINCT {by: dec.handle, status: dec.status}) AS deciders,
       newer.id AS superseded_by
"""

Q7 = """
CALL db.idx.fulltext.queryNodes('Message', $terms) YIELD node, score
MATCH (node)-[:MENTIONS]->(s:Service {name: $service})
MATCH (author:Person)-[:SENT]->(node)-[:IN_THREAD]->(th:Thread)
RETURN node.text AS text, author.handle AS by, th.channel AS channel,
       node.sent_at AS at, score, id(node) AS node_id
ORDER BY score DESC
LIMIT 5
"""

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

Q11 = """
CALL db.idx.fulltext.queryNodes('Option', $proposal_terms) YIELD node AS o, score
MATCH (d:Decision)-[rj:REJECTED]->(o)
MATCH (d)-[:AFFECTS]->(s:Service {name: $service})
OPTIONAL MATCH (d)-[:DOCUMENTED_IN]->(doc:Doc)
OPTIONAL MATCH (d)-[:CAUSED_BY]->(inc:Incident)
OPTIONAL MATCH (d)<-[:DECIDED]-(who:Person)
OPTIONAL MATCH (newer:Decision)-[:SUPERSEDES]->(d)
RETURN d.id AS decision, d.status AS status, o.text AS rejected_option,
       rj.reason AS reason, doc.title AS doc, inc.id AS incident, inc.title AS incident_title,
       collect(DISTINCT CASE WHEN who.status = 'active' THEN who.handle END) AS ask,
       newer.id AS superseded_by, score
ORDER BY score DESC LIMIT 3
"""

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


def rows(res):
    """Result set as a list of dicts keyed by column name."""
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    cols = [c.decode() if isinstance(c, bytes) else c for c in cols]
    return [dict(zip(cols, r)) for r in res.result_set]


def expect(cond, detail):
    if not cond:
        raise AssertionError(detail)
    return detail


# ---------------------------------------------------------------- checks
# Each check returns a short detail string on success and raises on failure.

def check_varlength(g):
    # Q1: who is upstream of payments-api within 4 hops, with the shortest hop count and one sample path
    r = rows(g.ro_query(Q1, {"service": "payments-api"}))
    got = {x["service"]: x["hops"] for x in r}
    expect(got == {"checkout": 1, "web": 2}, f"expected checkout:1, web:2, got {got}")
    web = next(x for x in r if x["service"] == "web")
    expect(len(web["path_node_ids"]) == 3, f"sample path for web should have 3 nodes, got {web['path_node_ids']}")
    return f"{got}; collect(p)[0] + nodes(p) OK"


def check_multitype(g):
    # Q4 uses [:AUTHORED|REVIEWED]; derive uses it on both sides of the pattern
    g.query(DERIVE_COLLAB)
    r = rows(g.ro_query(Q4, {"service": "payments-api", "k": 5}))
    expect([x["person"] for x in r] == ["marco", "sam"], f"expected [marco, sam], got {[x['person'] for x in r]}")
    expect(r[1]["recent_prs"][0] == "PR-2", f"sam's PRs should be newest first, got {r[1]['recent_prs']}")
    c = rows(g.ro_query("MATCH (:Person)-[c:COLLABORATES_WITH]->(:Person) RETURN count(c) AS n"))[0]["n"]
    expect(c == 4, f"expected 4 COLLABORATES_WITH edges (marco<->sam, marco<->priya), got {c}")
    return f"Q4 order {[x['person'] for x in r]}, sam PRs {r[1]['recent_prs']}; derive made {c} edges"


def check_fulltext_message(g):
    # Q7: full-text search on Message, then traverse to the service, author and thread
    r = rows(g.ro_query(Q7, {"terms": "timeout|retry|circuit*|%risk%", "service": "payments-api"}))
    expect(len(r) == 1 and r[0]["by"] == "lena", f"expected lena's warning, got {r}")
    return f"found lena's message in {r[0]['channel']} (score {r[0]['score']:.2f})"


def check_sppaths(g):
    # Q5: weighted shortest path from sam to priya, ignoring edge direction
    r = rows(g.ro_query(Q5, {"from": "sam", "to": "priya"}))
    expect(len(r) == 1, f"expected one path, got {r}")
    expect(r[0]["chain"] == ["sam", "marco", "priya"], f"expected sam->marco->priya, got {r[0]['chain']}")
    # direction check: lena -REPORTS_TO-> marco, so marco -> lena only exists with relDirection 'both'
    r2 = rows(g.ro_query(Q5, {"from": "marco", "to": "lena"}))
    expect(r2 and r2[0]["chain"] == ["marco", "lena"], f"reverse edge not followed: {r2}")
    return f"chain {r[0]['chain']} weight {r[0]['pathWeight']}; reverse REPORTS_TO followed"


def check_tofloat_case_sum(g):
    # Q10: agent share = sum(CASE ...) / sum(lines), zones from active experts
    r = {x["service"]: x for x in rows(g.ro_query(Q10, {"now": NOW, "threshold": 1.0}))}
    pa = r["payments-api"]
    expect(abs(pa["agent_share"] - 0.75) < 1e-9, f"payments-api agent_share should be 0.75, got {pa['agent_share']}")
    expect(pa["zone"] == "fragile" and pa["experts"] == ["marco"], f"payments-api should be fragile/[marco], got {pa}")
    expect(r["web"]["zone"] == "dark", f"web should be dark, got {r['web']}")
    expect(isinstance(r["web"]["agent_share"], float), "agent_share should be a float")
    return "payments-api share 0.75 fragile [marco]; web dark; " + ", ".join(f"{k}={v['zone']}" for k, v in r.items())


def check_fulltext_option(g):
    # Q11: decision guard matches a proposal against rejected options with | as OR
    r = rows(g.ro_query(Q11, {"proposal_terms": "idempoten*|dedupe*|duplicat*", "service": "ledger"}))
    expect(len(r) >= 1 and r[0]["decision"] == "ADR-009", f"expected ADR-009, got {r}")
    expect(r[0]["incident"] == "INC-007" and r[0]["ask"] == ["lena"], f"bad joins: {r[0]}")
    # control: a literal OR must not be needed; a non-matching term returns nothing
    none = rows(g.ro_query(Q11, {"proposal_terms": "kubernetes|helm*", "service": "ledger"}))
    expect(none == [], f"unrelated terms should not match, got {none}")
    return f"ADR-009 matched (score {r[0]['score']:.2f}), incident INC-007, ask {r[0]['ask']}"


def check_rel_uniqueness(g):
    # sam reviewed PR-1 twice. Known limitation: an unaliased edge in a pattern may be counted once.
    unaliased = rows(g.ro_query(
        "MATCH (:Person {handle:'sam'})-[:REVIEWED]->(:PR {id:'PR-1'}) RETURN count(*) AS n"))[0]["n"]
    aliased = rows(g.ro_query(
        "MATCH (:Person {handle:'sam'})-[r:REVIEWED]->(:PR {id:'PR-1'}) WHERE id(r) >= 0 RETURN count(*) AS n"))[0]["n"]
    expect(aliased == 2, f"aliased edge count should be 2, got {aliased}")
    note = "caveat REPRODUCES (unaliased undercounts)" if unaliased != 2 else "caveat not reproduced on this version"
    return f"unaliased={unaliased}, aliased={aliased} -> {note}; always alias edges in counts"


def uses_index(g, name, q, p):
    plan = g.explain(q, p)
    text = "\n".join(getattr(plan, "plan", None) or [str(plan)])
    expect("Index Scan" in text, f"{name} plan has no index scan:\n{text}")
    return f"{name} uses index"


def check_explain(g):
    # Plans for Q1/Q4 (as written in 04) should start from an index scan on Service(name)
    return ", ".join([uses_index(g, "Q1", Q1, {"service": "payments-api"}),
                      uses_index(g, "Q4", Q4, {"service": "payments-api", "k": 5})])


def check_explain_q4_fallback(g):
    # Fallback for item 8: same rows as Q4, but anchored on the Service index
    p = {"service": "payments-api", "k": 5}
    a = [(x["person"], x["recent_prs"]) for x in rows(g.ro_query(Q4, p))]
    b = [(x["person"], x["recent_prs"]) for x in rows(g.ro_query(Q4_FALLBACK, p))]
    expect(a == b, f"fallback rows differ: {a} vs {b}")
    return uses_index(g, "Q4_FALLBACK", Q4_FALLBACK, p) + f", same rows as Q4 {b}"


def check_not_in_listcomp(g):
    # Q14: what if marco leaves?
    r = {x["service"]: x for x in rows(g.ro_query(Q14, {"threshold": 1.0, "gone": ["marco"]}))}
    expect(set(r) == {"payments-api", "ledger"}, f"expected payments-api and ledger, got {list(r)}")
    expect(all(x["new_zone"] == "dark" and x["after"] == [] for x in r.values()), f"bad zones {r}")
    return "marco leaving -> " + ", ".join(f"{k} {v['before']}->{v['after']} {v['new_zone']}" for k, v in r.items())


def check_new_option_searchable(g):
    # Q15 3c: write a learned rule, then Q11 must find it straight away
    g.query("""
        MATCH (fs:Service {name:'fraud-svc'})
        MERGE (d:Decision {id: $decision_id})
        SET d.summary = 'Always keep a timeout on the fraud client', d.status = 'active', d.origin = 'learned'
        MERGE (r:Option {id: $decision_id + '-rejected'}) SET r.text = $rejected
        MERGE (d)-[rj:REJECTED]->(r) SET rj.reason = 'Caused the payments-api outage'
        MERGE (d)-[:AFFECTS]->(fs)
    """, {"decision_id": "LRN-001", "rejected": "Remove the timeout on the fraud client call"})
    r = rows(g.ro_query(Q11, {"proposal_terms": "timeout|fraud*", "service": "fraud-svc"}))
    expect(r and r[0]["decision"] == "LRN-001", f"new Option not found immediately: {r}")
    opt_id = rows(g.ro_query("MATCH (o:Option {id:'LRN-001-rejected'}) RETURN o.id AS id"))[0]["id"]
    return f"LRN-001 found right after write; $id + '-rejected' -> {opt_id}"


# ---- extras: syntax later blocks rely on (not in the Day-1 list)

def check_pagerank(g):
    # G1: critical services by PageRank over DEPENDS_ON
    r = rows(g.ro_query("""CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score
                           RETURN node.name AS service, score ORDER BY score DESC LIMIT 3"""))
    expect(len(r) == 3, f"got {r}")
    return "top: " + ", ".join(f"{x['service']}={x['score']:.3f}" for x in r)


def check_betweenness(g):
    # G2: bridge people on the collaboration graph
    r = rows(g.ro_query("""CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATES_WITH'],
                                                  samplingSize: 64, samplingSeed: 42})
                           YIELD node, score RETURN node.handle AS person, score ORDER BY score DESC"""))
    expect(r and r[0]["person"] == "marco", f"marco should be the bridge, got {r}")
    return "top: " + ", ".join(f"{x['person']}={x['score']:.2f}" for x in r[:3])


def check_label_propagation(g):
    # G3: shadow teams; YIELD then MATCH on the yielded node
    r = rows(g.ro_query("""CALL algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATES_WITH'],
                                                       maxIterations: 20})
                           YIELD node, communityId
                           MATCH (node)-[m:MEMBER_OF]->(t:Team) WHERE m.valid_to IS NULL
                           RETURN communityId, collect(node.handle) AS people, count(*) AS size"""))
    expect(len(r) >= 1, f"got {r}")
    return f"{len(r)} community(ies): " + "; ".join(str(x["people"]) for x in r)


def check_collect_distinct_map(g):
    # Q6: collect(DISTINCT {map}) plus OPTIONAL MATCH of a missing SUPERSEDES
    r = rows(g.ro_query(Q6, {"service": "ledger"}))
    expect(r and r[0]["deciders"] == [{"by": "lena", "status": "active"}] and r[0]["superseded_by"] is None, f"got {r}")
    return f"deciders {r[0]['deciders']}"


def check_create_with_unwind(g):
    # A1: CREATE ... WITH i UNWIND $downstream MATCH ... CREATE
    r = g.query("""
        MATCH (s:Service {name: $service}), (p:Person {handle: $assignee})
        CREATE (i:Incident {id: $id, title: $title, severity: 'sev1', opened_at: timestamp(), status: 'open'})
        CREATE (i)-[:IMPACTS {role: 'root'}]->(s)
        CREATE (i)-[:ASSIGNED_TO {reason: 'top who_knows'}]->(p)
        WITH i
        UNWIND $downstream AS name
        MATCH (x:Service {name: name})
        CREATE (i)-[:IMPACTS {role: 'downstream'}]->(x)
        RETURN i.id AS id""",
        {"service": "payments-api", "assignee": "marco", "id": "INC-100", "title": "payments-api down",
         "downstream": ["checkout", "web"]})
    n = rows(g.ro_query("MATCH (:Incident {id:'INC-100'})-[r:IMPACTS]->() RETURN count(r) AS n"))[0]["n"]
    expect(n == 3, f"expected 3 IMPACTS edges, got {n}")
    return f"INC-100 with {n} IMPACTS edges"


CHECKS = [
    ("1  Variable-length *1..4 + nodes(p) + min(length(p))", check_varlength),
    ("2  [:AUTHORED|REVIEWED] multi-type pattern", check_multitype),
    ("3  Full-text index create + queryNodes (Message, Q7)", check_fulltext_message),
    ("4  algo.SPpaths with relDirection: 'both' (Q5)", check_sppaths),
    ("5  toFloat() + CASE inside sum() (Q10)", check_tofloat_case_sum),
    ("6  Full-text on Option + '|' terms (Q11)", check_fulltext_option),
    ("7  Relationship-uniqueness caveat (alias edges)", check_rel_uniqueness),
    ("8  GRAPH.EXPLAIN on Q1/Q4 uses indexes", check_explain),
    ("8b Q4 fallback (anchor Service, WITH, then MATCH) uses index", check_explain_q4_fallback),
    ("9  NOT h IN $gone in list comprehension (Q14)", check_not_in_listcomp),
    ("10 New Option immediately searchable (Q15)", check_new_option_searchable),
    ("x1 algo.pageRank (G1)", check_pagerank),
    ("x2 algo.betweenness (G2)", check_betweenness),
    ("x3 algo.labelPropagation + MATCH after YIELD (G3)", check_label_propagation),
    ("x4 collect(DISTINCT {map}) (Q6)", check_collect_distinct_map),
    ("x5 CREATE ... WITH ... UNWIND ... CREATE (A1)", check_create_with_unwind),
]


def build(g):
    try:
        g.delete()
    except Exception:
        pass  # graph didn't exist yet
    for q in INDEXES:
        g.query(q)
    for q in SEED:
        g.query(q, {"now": NOW, "day": DAY})
    n = rows(g.ro_query("MATCH (n) RETURN count(n) AS n"))[0]["n"]
    e = rows(g.ro_query("MATCH ()-[r]->() RETURN count(r) AS n"))[0]["n"]
    return n, e


def main():
    db = FalkorDB(host=HOST, port=PORT)
    g = db.select_graph(GRAPH)
    n, e = build(g)
    print(f"Toy graph '{GRAPH}' built: {n} nodes, {e} edges  (FalkorDB at {HOST}:{PORT})\n")

    failed = 0
    for label, fn in CHECKS:
        try:
            detail = fn(g)
            print(f"PASS  {label}\n      {detail}")
        except Exception as ex:
            failed += 1
            msg = str(ex).strip() or ex.__class__.__name__
            print(f"FAIL  {label}\n      {ex.__class__.__name__}: {msg}")
            if os.getenv("VERBOSE"):
                traceback.print_exc()

    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed. View the toy graph at http://localhost:3000 (graph '{GRAPH}').")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
