import pytest

from kibana_mcp.core.dashboards.identity import derive_dashboard_id, normalize


def test_pinned_literal_ascii():
    # Locks the algorithm + encoding (NFC + casefold + slug + sha256-over-slug).
    assert derive_dashboard_id("Average Ticket Price") == "average-ticket-price-e65d17b32152"


def test_punctuation_and_case_variants_converge():
    same = {
        derive_dashboard_id("Q1 Sales"),
        derive_dashboard_id("Q1-Sales"),
        derive_dashboard_id("Q1_Sales"),
        derive_dashboard_id("  q1   sales  "),
    }
    assert same == {"q1-sales-ff5d6a46e163"}


def test_materially_different_titles_diverge():
    assert derive_dashboard_id("Sales") != derive_dashboard_id("Sales dashboard")


def test_punctuation_only_titles_use_fallback_stem_and_diverge():
    a = derive_dashboard_id("!!!")
    b = derive_dashboard_id("???")
    assert a.startswith("dashboard-") and b.startswith("dashboard-")
    assert a != b  # hashed over the normalized title, not the empty slug


def test_non_ascii_titles_diverge():
    assert derive_dashboard_id("売上") != derive_dashboard_id("利益")


def test_normalize_empty_for_whitespace_only():
    assert normalize("   \t\n ") == ""
    assert normalize("  Hello  World ") == "hello world"


def test_derive_rejects_blank_or_whitespace_only_title():
    # Belt-and-suspenders: no stable id exists for an empty normalized title, so
    # a direct caller fails closed instead of getting a shared "dashboard-<hash>".
    for blank in ("", "   ", "  \t\n "):
        with pytest.raises(ValueError):
            derive_dashboard_id(blank)


def test_id_is_deterministic_and_bounded():
    long = "A" * 500
    assert derive_dashboard_id(long) == derive_dashboard_id(long)
    assert len(derive_dashboard_id(long)) <= 77
