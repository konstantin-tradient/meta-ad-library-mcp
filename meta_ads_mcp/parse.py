"""Pure parsers for Meta Ad Library GraphQL/DOM payloads.

Kept dependency-free + side-effect-free so they can be unit-tested against the
saved fixtures without a browser. Schema confirmed live 2026-06-10 against
`AdLibraryV3AdDetailsQuery` (see fixtures/ad_details_sample.json).
"""
from __future__ import annotations

from typing import Any


def _best_body(snapshot: dict[str, Any]) -> str | None:
    """Ad copy lives in snapshot.body.text; fall back to other text fields so
    image/video ads still yield a description."""
    body = snapshot.get("body")
    if isinstance(body, dict) and body.get("text") and "{{" not in body["text"]:
        return body["text"]
    for k in ("title", "caption", "link_description"):
        if snapshot.get(k):
            return snapshot[k]
    for card in snapshot.get("cards") or []:
        if isinstance(card, dict) and card.get("body"):
            return card["body"]
    return None


def parse_search_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map AdLibrarySearchPaginationQuery / SSR `search_results_connection.edges`
    to flat ad rows. One row per ad (first collated result is representative)."""
    ads: list[dict[str, Any]] = []
    for edge in edges or []:
        results = ((edge or {}).get("node") or {}).get("collated_results") or []
        if not results:
            continue
        # multi-version ads: pick the version with the richest (longest) body,
        # skipping unrendered dynamic templates like "{{product.brand}}".
        def _score(res):
            b = _best_body(res.get("snapshot") or {}) or ""
            return -1 if "{{" in b else len(b)
        r = max(results, key=_score)
        snap = r.get("snapshot") or {}
        ads.append({
            "library_id": str(r.get("ad_archive_id")) if r.get("ad_archive_id") else None,
            "page_name": r.get("page_name") or snap.get("page_name"),
            "status": "active" if r.get("is_active") else "inactive",
            "body_text": _best_body(snap),
            "cta": snap.get("cta_text"),
            "link_url": snap.get("link_url"),
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "publisher_platform": r.get("publisher_platform"),
            "versions": r.get("collation_count"),
        })
    return ads


def parse_ad_details(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract description + EU/UK reach + demographic breakdown from an
    AdLibraryV3AdDetailsQuery response body (already JSON-parsed)."""
    details = (
        payload.get("data", {})
        .get("ad_library_main", {})
        .get("ad_details", {})
    )
    tbl = details.get("transparency_by_location") or {}
    eu = tbl.get("eu_transparency") or {}
    uk = tbl.get("uk_transparency") or {}

    breakdown = []
    for row in eu.get("age_country_gender_reach_breakdown") or []:
        breakdown.append({
            "country": row.get("country"),
            "age_gender": [
                {
                    "age_range": b.get("age_range"),
                    "male": b.get("male"),
                    "female": b.get("female"),
                    "unknown": b.get("unknown"),
                }
                for b in (row.get("age_gender_breakdowns") or [])
            ],
        })

    return {
        "eu_total_reach": eu.get("eu_total_reach"),
        "uk_total_reach": uk.get("total_reach"),
        "targets_eu": eu.get("targets_eu"),
        "location_audience": [
            la.get("name") for la in (eu.get("location_audience") or [])
        ],
        "gender_audience": eu.get("gender_audience"),
        "age_audience": eu.get("age_audience"),
        "reach_breakdown": breakdown,
    }
