"""set_service_status: mark a service down (incident simulation) or back up. The map turns it red."""

from api.db import Trace, graph

# Flip the service's status property; the UI colours down services and ripples their blast radius.
CYPHER = """
MATCH (s:Service {name: $service})
SET s.status = $status
RETURN s.name AS service, s.status AS status
"""


def set_service_status(service: str, status: str, g=None) -> dict:
    """status: 'down' | 'up'."""
    assert status in ("down", "up"), status
    t = Trace(g or graph())
    rows = t.write(CYPHER, {"service": service, "status": status})
    if not rows:
        raise ValueError(f"unknown service {service!r}")
    t.key("Service", service)
    return t.result(rows)
