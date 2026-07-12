"""Tests unitarios para el servidor MCP Wahapedia (WH40K)."""

from wahapedia.server import (
    FACTIONS,
    _normalize_query,
    _resolve_faction_slug,
    _slugify,
)


def test_slugify_basico():
    assert _slugify("Saint Celestine") == "Saint-Celestine"
    assert _slugify("  Necron Warrior ") == "Necron-Warrior"


def test_normalize_query_quita_no_alphanumeric():
    assert _normalize_query("Space Marine!") == "spacemarine"
    assert _normalize_query("Ork's Boyz") == "orksboyz"


def test_resolve_faction_slug_exacto():
    assert _resolve_faction_slug("space-marines") == "space-marines"
    assert _resolve_faction_slug("adeptus custodes") == "adeptus-custodes"


def test_resolve_faction_slug_inexistente():
    assert _resolve_faction_slug("no-existe-esta-faccion") is None
    assert _resolve_faction_slug("faccion inventada 123") is None


def test_factions_conocidas_no_vacias():
    assert isinstance(FACTIONS, list)
    assert len(FACTIONS) > 0
    assert "space-marines" in FACTIONS
    assert "tyranids" in FACTIONS


def test_resolve_faction_slug_case_insensitive():
    assert _resolve_faction_slug("TYRANIDS") == "tyranids"
    assert _resolve_faction_slug("Necrons") == "necrons"
