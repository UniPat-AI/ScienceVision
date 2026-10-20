"""Pin the 12-domain taxonomy to canonical order, ids, colors, and shipped flags.

Accidental reordering, renaming, or color drift fails CI. To intentionally
change the taxonomy, update this test in the same commit.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "viewer" / "manifest.json"

# Canonical 12-domain order (homepage display order).
EXPECTED_DOMAINS = [
    ("biology",               "Biology",               "#f78166", True ),
    ("medical",               "Medical",               "#56d364", True ),
    ("embodied",              "Embodied",              "#ffa657", True ),
    ("robotics",              "Robotics",              "#ff9a47", False),
    ("geography",             "Geography",             "#79c0ff", False),
    ("astronomy",             "Astronomy",             "#d2a8ff", True ),
    ("topological_photonics", "Topological Photonics", "#ff7b72", False),
    ("photonics",             "Photonics",             "#ffd166", False),
    ("simulation",            "Simulation",            "#a5d6ff", False),
    ("climate",               "Climate",               "#3fb950", False),
    ("industrial",            "Industrial",            "#c79aff", True ),
    ("chemistry",             "Chemistry",             "#db61a2", True ),
]


def _load_domains() -> list[dict]:
    with MANIFEST_PATH.open() as fh:
        return json.load(fh)["domains"]


def test_domain_count():
    assert len(_load_domains()) == 12


def test_domain_order_and_ids():
    actual = [(d["id"], d["label"], d["color"], d["shipped"]) for d in _load_domains()]
    assert actual == EXPECTED_DOMAINS


def test_shipped_domains():
    shipped = [d["id"] for d in _load_domains() if d["shipped"]]
    assert sorted(shipped) == [
        "astronomy", "biology", "chemistry", "embodied", "industrial", "medical",
    ]


def test_no_duplicate_colors():
    colors = [d["color"] for d in _load_domains()]
    assert len(colors) == len(set(colors)), \
        f"duplicate colors in domains[]: {colors}"


def test_all_ids_are_snake_case():
    import re
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for d in _load_domains():
        assert pattern.match(d["id"]), f"bad id: {d['id']}"
