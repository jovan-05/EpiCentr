"""Keystone graph tools. One file per tool; each holds its Cypher.

Every tool returns {rows, keys, cypher, params, ms} (see api/db.py).
READ tools only use ro_query; WRITE tools are the agent's actions.
"""

from api.tools.blast_radius import blast_radius
from api.tools.check_decision import check_decision
from api.tools.cite import cite
from api.tools.comprehension_map import comprehension_map
from api.tools.current_owners import current_owners
from api.tools.danger_ranking import danger_ranking
from api.tools.decision_trace import decision_trace
from api.tools.edges_between import edges_between
from api.tools.file_risk import file_risk
from api.tools.knowledge_spread import knowledge_spread
from api.tools.log_guard import log_guard
from api.tools.open_incident import open_incident
from api.tools.prior_warnings import prior_warnings
from api.tools.resolve_incident import resolve_incident
from api.tools.services_down import services_down
from api.tools.set_service_status import set_service_status
from api.tools.stamp_chains import stamp_chains
from api.tools.suspect_changes import suspect_changes
from api.tools.what_if import what_if
from api.tools.who_knows import who_knows

READ = {f.__name__: f for f in (blast_radius, current_owners, who_knows, decision_trace, prior_warnings,
                                comprehension_map, stamp_chains, knowledge_spread, suspect_changes, what_if,
                                check_decision, services_down, danger_ranking)}
WRITE = {f.__name__: f for f in (open_incident, cite, file_risk, log_guard, set_service_status, resolve_incident)}
TOOLS = {**READ, **WRITE}
