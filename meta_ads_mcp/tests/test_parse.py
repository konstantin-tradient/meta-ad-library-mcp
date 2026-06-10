"""Parser tests against the real captured AdLibraryV3AdDetailsQuery fixture."""
import json
import pathlib

from meta_ads_mcp.parse import parse_ad_details

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "ad_details_sample.json"


def test_parse_real_fixture():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out = parse_ad_details(payload)
    assert isinstance(out["eu_total_reach"], int) and out["eu_total_reach"] > 0
    assert out["reach_breakdown"], "expected per-country breakdown rows"
    first = out["reach_breakdown"][0]
    assert "country" in first and first["age_gender"]
    ag = first["age_gender"][0]
    assert {"age_range", "male", "female", "unknown"} <= set(ag)
