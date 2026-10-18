"""One test per read tool, asserting the story facts it must surface (06), plus the shared contract."""

from importlib import import_module

import pytest

from api.tools import (READ, blast_radius, check_decision, comprehension_map, current_owners, danger_ranking,
                       decision_trace, knowledge_spread, prior_warnings, services_down, stamp_chains, suspect_changes,
                       what_if, who_knows)


def _mod(name):
    """The tool's module (api.tools re-exports the functions under the same names)."""
    return import_module(f"api.tools.{name}")


m_blast, m_owners, m_who, m_trace, m_stamp, m_spread, m_suspect, m_whatif, m_check, m_warn = map(_mod, [
    "blast_radius", "current_owners", "who_knows", "decision_trace", "stamp_chains", "knowledge_spread",
    "suspect_changes", "what_if", "check_decision", "prior_warnings"])

# ---------------------------------------------------------------- contract

SAMPLE_CALLS = {
    "blast_radius": dict(service="payments-api"),
    "current_owners": dict(service="payments-api"),
    "who_knows": dict(service="payments-api"),
    "decision_trace": dict(service="payments-api"),
    "prior_warnings": dict(service="payments-api", terms="timeout|circuit*"),
    "comprehension_map": dict(),
    "stamp_chains": dict(service="ledger"),
    "knowledge_spread": dict(service="ledger"),
    "suspect_changes": dict(service="payments-api"),
    "what_if": dict(gone=["marco"]),
    "check_decision": dict(service="ledger", proposal="remove the idempotency check", log=False),
    "services_down": dict(down=["payments-api"]),
    "danger_ranking": dict(),
}


def test_every_read_tool_has_a_sample_call():
    assert set(SAMPLE_CALLS) == set(READ)


@pytest.mark.parametrize("name", sorted(SAMPLE_CALLS))
def test_contract(g, name):
    """Every tool returns {rows, keys, cypher, params, ms}; keys are 'Label:key' strings; values are passed as $params."""
    res = READ[name](g=g, **SAMPLE_CALLS[name])
    assert {"rows", "keys", "cypher", "params", "ms"} <= set(res)
    assert isinstance(res["rows"], list) and res["rows"]
    assert res["keys"] and all(":" in k for k in res["keys"])
    assert "$" in res["cypher"] and isinstance(res["ms"], float)


# Every per-service query must start from an index (Block A: 04's Q4 silently did a full Person scan).
ANCHORED = [("blast_radius", m_blast.CYPHER, {"service": "x"}),
            ("current_owners", m_owners.CYPHER, {"service": "x", "week": "w"}),
            ("who_knows", m_who.CYPHER, {"service": "x", "k": 5}),
            ("decision_trace", m_trace.CYPHER, {"service": "x"}),
            ("stamp_chains", m_stamp.CYPHER, {"service": "x"}),
            ("knowledge_spread", m_spread.CYPHER, {"service": "x"}),
            ("suspect_changes", m_suspect.CYPHER, {"service": "x", "since": 0}),
            ("what_if learners", m_whatif.LEARNERS, {"service": "x", "gone": []})]
FULLTEXT = [("check_decision", m_check.CYPHER, {"service": "x", "terms": "a"}),
            ("prior_warnings messages", m_warn.MESSAGES, {"service": "x", "terms": "a"}),
            ("prior_warnings tickets", m_warn.TICKETS, {"service": "x", "terms": "a"})]


@pytest.mark.parametrize("name,cypher,params", ANCHORED, ids=[a[0] for a in ANCHORED])
def test_query_starts_from_an_index(g, name, cypher, params):
    plan = "\n".join(g.explain(cypher, params).plan)
    assert "Index Scan" in plan, plan


@pytest.mark.parametrize("name,cypher,params", FULLTEXT, ids=[a[0] for a in FULLTEXT])
def test_fulltext_query_uses_procedure(g, name, cypher, params):
    plan = "\n".join(g.explain(cypher, params).plan)
    assert "ProcedureCall" in plan, plan


# ---------------------------------------------------------------- story facts per tool

def test_blast_radius(g):
    rows = blast_radius("payments-api", g=g)["rows"]
    hops = {r["service"]: r["hops"] for r in rows}
    assert hops["checkout-api"] == 1 and hops["checkout-web"] == 2           # Story 2 chain
    assert {"support-api", "mobile-bff", "api-gateway"} <= set(hops)
    assert rows[0]["path"] == ["payments-api", "checkout-api"]               # tier 1 + nearest first
    assert blast_radius("payments-api", max_hops=1, g=g)["rows"] and \
        all(r["hops"] == 1 for r in blast_radius("payments-api", max_hops=1, g=g)["rows"])


def test_current_owners(g):
    res = current_owners("payments-api", g=g)
    row = res["rows"][0]
    assert row["team"] == "payments" and row["on_call"] == "priya" and row["on_call_departed"]   # Story 1
    assert "priya" not in [m["handle"] for m in row["members"]] and row["active_members"] == 5
    assert {"Team:payments", "Person:priya"} | {f"Person:{m['handle']}" for m in row["members"]} <= set(res["keys"])
    assert current_owners("referrals", g=g)["orphaned"]                                          # Story 5


def test_who_knows(g):
    res = who_knows("payments-api", k=10, g=g)
    people = [r["person"] for r in res["rows"]]
    assert people[0] == "marco" and people.index("sam") > 0                   # Story 1
    assert "priya" not in people                                              # departed people never suggested
    assert res["rows"][0]["recent_prs"][0] == "PR-4452" and len(res["rows"][0]["recent_prs"]) == 3
    assert res["keys"][:2] == ["Service:payments-api", "Person:marco"]


def test_decision_trace(g):
    rows = decision_trace("payments-api", g=g)["rows"]
    adr = next(r for r in rows if r["decision"] == "ADR-017")                # Story 2
    assert adr["origin"][0]["by"] == "priya" and adr["origin"][0]["channel"] == "#payments-arch"
    assert sorted(adr["deciders"]) == ["dana", "priya"] and adr["superseded_by"] is None
    assert adr["docs"] == ["DOC-ADR-017"] and adr["rejected"]


def test_prior_warnings(g):
    rows = prior_warnings("payments-api", "timeout|circuit*", g=g)["rows"]   # Story 3
    msg = next(r for r in rows if r["kind"] == "message")
    assert msg["by"] == "lena" and msg["channel"] == "#risk-eng"
    assert [r["id"] for r in rows if r["kind"] == "ticket"] == ["RISK-88"]
    assert prior_warnings("ledger", "timeout|circuit*", g=g)["rows"] == []   # scoped to the service


def test_comprehension_map(g):
    rows = {r["service"]: r for r in comprehension_map(g=g)["rows"]}         # Story 6
    assert rows["ledger"]["zone"] == "dark" and 0.65 < rows["ledger"]["agent_share"] < 0.75
    assert rows["payments-api"]["experts"] == ["marco"]
    assert comprehension_map(g=g)["rows"][0]["service"] == "ledger"           # the worst critical zone first


def test_stamp_chains(g):
    rows = stamp_chains("ledger", g=g)["rows"]
    assert rows and all(r["agent"] == "codegen-bot" and r["secs"] < 90 and r["prompted_by"] == "ivy" for r in rows)
    assert {r["approver"] for r in rows} <= {"sam", "noor"}


def test_knowledge_spread(g):
    r = knowledge_spread("ledger", g=g)["rows"][0]
    assert (r["mentor"], r["learner"]) == ("dana", "ivy")


def test_suspect_changes(g):
    rows = suspect_changes("payments-api", g=g)["rows"]                      # Story 8
    top = rows[0]
    assert top["pr"] == "PR-4471" and top["agent_author"] == "codegen-bot" and top["prompted_by"] == "ivy"
    assert top["reviews"] == [{"by": "sam", "secs": 38, "comments": 0}] and top["rubber_stamped"]
    assert top["hours_ago"] == 3.0


def test_what_if(g):
    rows = what_if(["marco"], g=g)["rows"]
    assert [(r["service"], r["new_zone"]) for r in rows] == [("payments-api", "dark")]
    assert rows[0]["learners"][0]["learner"] == "ivy"
    assert what_if([], g=g)["rows"] == []


def test_what_if_summary_is_a_before_after_diff(g):
    s = what_if(["marco"], g=g)["summary"]
    assert s["before"] == {"dark": 2, "fragile": 6, "healthy": 14} and s["services"] == 22
    assert s["after"] == {"dark": 3, "fragile": 5, "healthy": 14} and s["tier1_worse"] == 1   # payments-api fragile -> dark
    nobody = what_if([], g=g)["summary"]
    assert nobody["before"] == nobody["after"]


def test_services_down(g):
    res = services_down(["payments-api"], g=g)
    names = {r["service"] for r in res["rows"]}
    assert names == {r["service"] for r in blast_radius("payments-api", g=g)["rows"]}            # same as Q1 for one service
    assert res["summary"]["tier1"] == 3 and res["summary"]["call"][0]["person"] == "marco"
    assert [r["pagerank"] for r in res["rows"]] == sorted((r["pagerank"] for r in res["rows"]), reverse=True)
    both = services_down(["config-svc", "event-bus"], g=g)                                      # merged, no duplicates
    assert len({r["service"] for r in both["rows"]}) == len(both["rows"]) == both["summary"]["impacted"]
    assert not {"config-svc", "event-bus"} & {r["service"] for r in both["rows"]}
    assert both["summary"]["impacted"] > res["summary"]["impacted"]


def test_danger_ranking(g):
    rows = danger_ranking(g=g)["rows"]
    top = rows[0]
    assert top["person"] == "marco" and [d["service"] for d in top["dark"]] == ["payments-api"]   # Story 5 + what-if
    assert top["bridge_rank"] == 1                                                                 # also the G2 bridge
    # Same answer as 04's per-person loop: each person's dark list = what_if([person]) services that go dark.
    for r in rows[:5]:
        loop = {x["service"] for x in what_if([r["person"]], g=g)["rows"] if x["new_zone"] == "dark"}
        assert {d["service"] for d in r["dark"]} == loop, r["person"]
    assert [r["risk"] for r in rows] == sorted((r["risk"] for r in rows), reverse=True)


@pytest.mark.parametrize("service,proposal,verdict", [
    ("ledger", "remove the idempotency check in ledger to cut write latency", "block"),   # Story 7
    ("ledger", "dedupe payouts in the mobile client only", "block"),
    ("ledger", "add an idempotency check to the ledger payout path", "warn"),             # what ADR-009 chose
    ("ledger", "bump the logging library", "ok"),
    ("payments-api", "remove the timeout in the fraud client", "ok"),                     # until Block G's lesson
])
def test_check_decision(g, service, proposal, verdict):
    res = check_decision(service, proposal, log=False, g=g)
    assert res["verdict"] == verdict, res["reason"]
    if verdict == "block" and "idempotency" in proposal:
        assert "ADR-009" in res["reason"] and "INC-007" in res["reason"] and "@dana" in res["reason"]
        assert "Person:dana" in res["keys"] and "Incident:INC-007" in res["keys"]


def test_guard_terms_never_contain_a_hyphen(g):
    # In the full-text syntax '-' means NOT: 'dedupe-check*' = "dedupe but NOT check", which drops ADR-009's own
    # rejected option ('Remove dedupe check for speed'). Hyphenated words must become separate terms.
    from api.tools.check_decision import clean_terms, terms_from_text
    assert terms_from_text("the fraud-svc client") == "fraud*|client*"
    assert clean_terms("fraud-svc*|timeout") == "fraud|svc*|timeout"
    assert check_decision("ledger", "remove the dedupe-check for speed", log=False, g=g)["verdict"] == "block"
    assert check_decision("ledger", "remove dedupe check", terms="remove*|dedupe-check*", log=False,
                          g=g)["verdict"] == "block"


def test_check_decision_accepts_code_dir_names(g):
    # MCP / hook callers pass the folder name (services/ledger, services/payments_api); graph names are kebab-case.
    a = check_decision("payments_api", "remove the timeout on the fraud check", log=False, g=g)
    b = check_decision("payments-api", "remove the timeout on the fraud check", log=False, g=g)
    assert a["verdict"] == b["verdict"] and a["params"]["service"] == "payments-api"
