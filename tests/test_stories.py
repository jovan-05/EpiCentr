"""Every planted story fact must hold in data/northwind (one test per story)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_stories  # noqa: E402

WORLD = check_stories.World(*check_stories.load())


@pytest.mark.parametrize("title,fn", check_stories.STORIES, ids=[t for t, _ in check_stories.STORIES])
def test_story(title, fn):
    failures = [f"{label} -> {detail}" for label, ok, detail in fn(WORLD) if not ok]
    assert not failures, "\n".join(failures)
