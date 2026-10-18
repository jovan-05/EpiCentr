"""Load data/northwind into FalkorDB (Block C). The logic lives in api/seed.py (the API reuses it on boot).

Run:  .venv\\Scripts\\python.exe scripts\\load.py [graph]   (then scripts\\derive.py)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.db import graph  # noqa: E402
from api.seed import FULLTEXT, load  # noqa: E402,F401  (re-exported for tests)


def connect(name=None):
    return graph(name)


def main():
    g = connect(sys.argv[1] if len(sys.argv) > 1 else None)
    t = time.time()
    meta, _ = load(g)
    n = g.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    e = g.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    expected_n = sum(meta["counts"]["nodes"].values()) + 1   # + the Dataset node
    expected_e = sum(meta["counts"]["edges"].values())
    print(f"Loaded graph '{g.name}' in {time.time() - t:.1f}s: {n} nodes (expected {expected_n}), {e} edges (expected {expected_e})")
    print(f"Demo clock (Dataset.as_of): {meta['as_of_iso']}, week {meta['as_of_week']}")
    if (n, e) != (expected_n, expected_e):
        sys.exit("count mismatch")


if __name__ == "__main__":
    main()
