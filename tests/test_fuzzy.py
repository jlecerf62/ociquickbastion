from qb2.util import fuzzy_filter


def test_fuzzy_filter_basic():
    items = ["alpha", "beta", "gamma", "delta"]
    assert fuzzy_filter(items, "alp")[0] == "alpha"
    assert "beta" in fuzzy_filter(items, "be")


def test_fuzzy_filter_substring_fallback():
    items = ["Compute-01", "DbNode-02", "Network-03"]
    hits = fuzzy_filter(items, "node")
    assert hits and hits[0] == "DbNode-02"
