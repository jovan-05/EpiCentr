"""GET /map: everything the 3D view draws, in one call (~95 nodes: services, teams, people, agents, open incidents).

Node ids are the same "Label:key" strings tools return in `keys`, so the UI highlights a claim's path
by looking ids up directly.
"""

from api import scoring
from api.db import Trace, clock, graph
from api.tools import comprehension_map

# Services with tier, live status and description. (Zone / agent share / experts come from comprehension_map.)
SERVICES = "MATCH (s:Service) RETURN s.name AS name, s.tier AS tier, s.status AS status, s.description AS description"

# PageRank over dependencies: the UI sizes services by how much depends on them (G1).
PAGERANK = "CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score RETURN node.name AS name, score"

# Everyone, with their team (departed people keep their last team and are drawn as ghosts).
PEOPLE = """
MATCH (p:Person)
OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
RETURN p.handle AS handle, p.name AS name, p.role AS role, p.status AS status, t.slug AS team
"""

TEAMS = "MATCH (t:Team) RETURN t.slug AS slug, t.name AS name, t.group AS grp"
AGENTS = "MATCH (a:CodingAgent) RETURN a.name AS name"

# Service -> service calls.
DEPENDS = "MATCH (a:Service)-[d:DEPENDS_ON]->(b:Service) RETURN a.name AS src, b.name AS dst, d.mode AS mode"

# What the org chart says: current ownership and membership.
OWNS = "MATCH (t:Team)-[o:OWNS]->(s:Service) WHERE o.valid_to IS NULL RETURN t.slug AS src, s.name AS dst"
MEMBERS = "MATCH (p:Person)-[m:MEMBER_OF]->(t:Team) WHERE m.valid_to IS NULL RETURN p.handle AS src, t.slug AS dst"

# What the activity proves: experts (score >= threshold), including departed ones (shown as fading links).
UNDERSTANDS = """
MATCH (p:Person)-[u:UNDERSTANDS]->(s:Service) WHERE u.score >= $threshold
RETURN p.handle AS src, s.name AS dst, u.score AS score, p.status AS status
"""

# How much each coding agent wrote into each service in the last 90 days.
AGENT_WROTE = """
MATCH (ag:CodingAgent)-[:AUTHORED]->(pr:PR)-[:TOUCHES]->(s:Service)
WHERE pr.merged_at > $now - 7776000000
RETURN ag.name AS src, s.name AS dst, count(pr) AS prs, sum(pr.lines) AS lines
"""

# Open incidents, what they impact and who they're assigned to.
INCIDENTS = """
MATCH (i:Incident {status: 'open'})-[r:IMPACTS]->(s:Service)
OPTIONAL MATCH (i)-[:ASSIGNED_TO]->(p:Person)
RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status, r.role AS role, s.name AS service,
       p.handle AS assignee
"""

# Rules learned from incidents (gold diamonds), what they protect and the incident they came from.
LEARNED = """
MATCH (d:Decision {origin: 'learned'})
OPTIONAL MATCH (d)-[:AFFECTS]->(s:Service)
OPTIONAL MATCH (d)-[:CAUSED_BY]->(i:Incident)
RETURN d.id AS id, d.summary AS summary, d.status AS status,
       collect(DISTINCT s.name) AS affects, collect(DISTINCT i.id) AS incidents
"""

# The latest decision-guard calls (audit trail) and the decision each one matched.
GUARD_CHECKS = """
MATCH (g:GuardCheck)
WITH g ORDER BY g.at DESC LIMIT 6
OPTIONAL MATCH (g)-[:MATCHED]->(d:Decision)
OPTIONAL MATCH (d)-[:AFFECTS]->(s:Service)
RETURN g.id AS id, g.verdict AS verdict, g.caller AS caller, g.service AS service, g.proposal AS proposal, g.at AS at,
       d.id AS decision, d.summary AS summary, d.status AS status, d.origin AS origin, collect(DISTINCT s.name) AS affects
"""

# Incidents behind learned rules, even when resolved (so the rule stays wired to its incident).
INCIDENTS_BY_ID = """
MATCH (i:Incident) WHERE i.id IN $ids
OPTIONAL MATCH (i)-[r:IMPACTS {role: 'root'}]->(s:Service)
RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status, s.name AS service
"""


def build_map(threshold=scoring.THRESHOLD, g=None):
    g = g or graph()
    now, week = clock(g)
    t = Trace(g)
    zones = {r["service"]: r for r in comprehension_map(threshold=threshold, g=g)["rows"]}
    pr = {r["name"]: r["score"] for r in t.read(PAGERANK)}
    nodes, links = [], []

    for s in t.read(SERVICES):
        z = zones[s["name"]]
        nodes.append({"id": f"Service:{s['name']}", "type": "Service", "label": s["name"], "tier": s["tier"],
                      "status": s["status"], "description": s["description"], "zone": z["zone"],
                      "agent_share": round(z["agent_share"], 3), "experts": z["experts"], "pagerank": pr.get(s["name"], 0)})
    for tm in t.read(TEAMS):
        nodes.append({"id": f"Team:{tm['slug']}", "type": "Team", "label": tm["name"], "group": tm["grp"]})
    for p in t.read(PEOPLE):
        nodes.append({"id": f"Person:{p['handle']}", "type": "Person", "label": p["handle"], "name": p["name"],
                      "role": p["role"], "status": p["status"], "team": p["team"]})
    for a in t.read(AGENTS):
        nodes.append({"id": f"CodingAgent:{a['name']}", "type": "CodingAgent", "label": a["name"]})

    for r in t.read(DEPENDS):
        links.append({"source": f"Service:{r['src']}", "target": f"Service:{r['dst']}", "type": "DEPENDS_ON", "mode": r["mode"]})
    for r in t.read(OWNS):
        links.append({"source": f"Team:{r['src']}", "target": f"Service:{r['dst']}", "type": "OWNS"})
    for r in t.read(MEMBERS):
        links.append({"source": f"Person:{r['src']}", "target": f"Team:{r['dst']}", "type": "MEMBER_OF"})
    for r in t.read(UNDERSTANDS, {"threshold": threshold}):
        links.append({"source": f"Person:{r['src']}", "target": f"Service:{r['dst']}", "type": "UNDERSTANDS",
                      "score": r["score"], "departed": r["status"] == "departed"})
    for r in t.read(AGENT_WROTE, {"now": now}):
        links.append({"source": f"CodingAgent:{r['src']}", "target": f"Service:{r['dst']}", "type": "AGENT_WROTE",
                      "prs": r["prs"], "lines": r["lines"]})

    seen = set()
    for r in t.read(INCIDENTS):
        iid = f"Incident:{r['id']}"
        if iid not in seen:
            seen.add(iid)
            nodes.append({"id": iid, "type": "Incident", "label": r["id"], "title": r["title"], "severity": r["severity"],
                          "status": r["status"]})
            if r["assignee"]:
                links.append({"source": iid, "target": f"Person:{r['assignee']}", "type": "ASSIGNED_TO"})
        links.append({"source": iid, "target": f"Service:{r['service']}", "type": "IMPACTS", "role": r["role"]})

    decisions = {}
    for r in t.read(LEARNED):
        decisions[r["id"]] = {**r, "origin": "learned"}
    guards = t.read(GUARD_CHECKS)
    for r in guards:
        if r["decision"] and r["decision"] not in decisions:
            decisions[r["decision"]] = {"id": r["decision"], "summary": r["summary"], "status": r["status"],
                                        "origin": r["origin"] or "seed", "affects": r["affects"], "incidents": []}
    behind = sorted({i for d in decisions.values() for i in d["incidents"]} - {s.split(":", 1)[1] for s in seen})
    for r in (t.read(INCIDENTS_BY_ID, {"ids": behind}) if behind else []):
        iid = f"Incident:{r['id']}"
        seen.add(iid)
        nodes.append({"id": iid, "type": "Incident", "label": r["id"], "title": r["title"], "severity": r["severity"],
                      "status": r["status"]})
        if r["service"]:
            links.append({"source": iid, "target": f"Service:{r['service']}", "type": "IMPACTS", "role": "root"})
    for d in decisions.values():
        did = f"Decision:{d['id']}"
        nodes.append({"id": did, "type": "Decision", "label": d["id"], "summary": d["summary"], "status": d["status"],
                      "origin": d["origin"]})
        for s in d["affects"]:
            links.append({"source": did, "target": f"Service:{s}", "type": "AFFECTS"})
        for i in d["incidents"]:
            links.append({"source": did, "target": f"Incident:{i}", "type": "CAUSED_BY"})
    for r in guards:
        gid = f"GuardCheck:{r['id']}"
        nodes.append({"id": gid, "type": "GuardCheck", "label": r["verdict"], "verdict": r["verdict"], "caller": r["caller"],
                      "service": r["service"], "proposal": r["proposal"], "at": r["at"]})
        links.append({"source": gid, "target": f"Decision:{r['decision']}" if r["decision"] else f"Service:{r['service']}",
                      "type": "MATCHED" if r["decision"] else "CHECKED"})

    # Drop links to nodes that aren't drawn (e.g. an old guard check on a misspelled service): the 3D engine
    # throws on a dangling link and the whole view goes blank.
    ids = {n["id"] for n in nodes}
    links = [l for l in links if l["source"] in ids and l["target"] in ids]
    return {"nodes": nodes, "links": links, "clock": {"now": now, "week": week}, "threshold": threshold,
            "counts": {"nodes": len(nodes), "links": len(links)}, "cypher": "\n\n".join(t.cypher), "ms": round(t.ms, 3)}
