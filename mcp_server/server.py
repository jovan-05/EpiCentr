"""Keystone MCP server: the same graph tools the agent and the API use, for any MCP client (Claude Code, etc.).

One code path, two front doors: these functions call api/tools directly, so every answer is the same Cypher
on FalkorDB. Read-only, except that check_decision logs a GuardCheck node (the audit trail).

Run (stdio):  .venv\\Scripts\\python.exe mcp_server\\server.py
Registered for the demo in demo-repo/.mcp.json.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import config  # noqa: E402,F401  (loads .env: FALKORDB_HOST / PORT / GRAPH)
from api import tools  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

server = MCPServer(
    "keystone",
    instructions="Keystone is a company brain on a FalkorDB graph of services, people, PRs, decisions and incidents. "
                 "Before changing behaviour in a service, call check_decision. Use who_knows to find the right "
                 "reviewer and decision_trace to learn why the code is the way it is. Every answer lists the graph "
                 "keys it is based on.",
)


def _out(res):
    """Rows + the graph keys they came from + the Cypher that ran (explainable answers)."""
    keep = {k: res[k] for k in ("rows", "keys", "ms", "cypher")}
    for k in ("verdict", "reason", "guard_check", "orphaned"):
        if k in res:
            keep[k] = res[k]
    return keep


@server.tool()
def check_decision(service: str, proposal: str) -> dict:
    """Is this proposed change something the team already rejected? Returns verdict block / warn / ok with the
    decision, the incident behind it and who to ask. Call this before changing behaviour in a service."""
    return _out(tools.check_decision(service, proposal, caller="mcp"))


@server.tool()
def who_knows(service: str, k: int = 5) -> dict:
    """Active people who actually understand a service (from PRs authored, real reviews and incidents), best first."""
    return _out(tools.who_knows(service, k=k))


@server.tool()
def decision_trace(service: str) -> dict:
    """Why a service is built the way it is: decisions, who made them, rejected options, where they were proposed."""
    return _out(tools.decision_trace(service))


@server.tool()
def blast_radius(service: str) -> dict:
    """Services that break if this one goes down, nearest and most critical first."""
    return _out(tools.blast_radius(service))


@server.tool()
def current_owners(service: str) -> dict:
    """Owner team, members and this week's on-call (flags an on-call person who has left)."""
    return _out(tools.current_owners(service))


@server.tool()
def suspect_changes(service: str, hours: int = 72) -> dict:
    """Recent PRs on a service: agent or human author, who prompted the agent, and whether review was a rubber stamp."""
    return _out(tools.suspect_changes(service, hours=hours))


@server.tool()
def prior_warnings(service: str, terms: str) -> dict:
    """Chat messages and open tickets about a service matching full-text terms ('|' = OR, '*' = prefix)."""
    return _out(tools.prior_warnings(service, terms))


@server.tool()
def comprehension_map() -> dict:
    """Every service's zone (dark / fragile / healthy), share of agent-written code, and active human experts."""
    return _out(tools.comprehension_map())


@server.tool()
def what_if(gone: list[str]) -> dict:
    """Which services lose their experts if these people leave, and who should learn what."""
    return _out(tools.what_if(gone))


@server.tool()
def services_down(down: list[str]) -> dict:
    """What breaks if these services go down (merged blast radius, ranked by PageRank) and who to call."""
    return _out(tools.services_down(down))


@server.tool()
def danger_ranking(limit: int = 10) -> dict:
    """Who is most dangerous to lose: people who are the only (or one of two) experts on services, with betweenness."""
    return _out(tools.danger_ranking(limit=limit))


if __name__ == "__main__":
    server.run("stdio")
