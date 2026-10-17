# Changelog

## 0.3.0 — 2026-10-18

- Add optional portrait PNG weather, driving and rail itinerary cards with Chinese fonts and source timestamps.
- Include current weather, apparent temperature and the next six hourly forecasts without substituting missing values.
- Export selected route plans with `--image`; retain text and structured results if image generation fails.
- Add a bounded offline JSON-to-image CLI that preserves original query metadata and accepts railway round-trip output.
- Keep rendered images private and document native Bot attachment delivery with a text fallback.

## 0.2.0 — 2026-10-18

- Add dates, ordered stops, per-leg timing, departure comparisons and versioned JSON responses.
- Convert GCJ-02 map coordinates to WGS84 for weather; preserve unknown weather and real forecast horizons.
- Discover bounded scenic candidates and assess driving detours while retaining original via points.
- Add time-budget round trips, sourced public addresses, private preferences and solar-time night checks.
- Generate Markdown itinerary cards and private watch baselines with meaningful-change deduplication.
- Protect preferences and snapshots in Git, retain attribution and document service limitations.
