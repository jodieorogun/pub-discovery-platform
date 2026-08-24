# Audit catalogue

Audits are tracked, compact evidence of decisions and historical measurements:

- `feature_coverage_expansion_2026-08-12.json`: current corpus coverage and quality snapshot.
- `ragas_evidence_evaluation_2026-08-12.json`: current evidence-layer methodology and metrics.
- `regression_300_baseline_2026-08-12.json`: pre-cleanup regression baseline.
- `sqlite_events_metrics_2026-08-12.json`: post-cleanup SQLite/FTS5, Camden music/event
  coverage, 300-case retrieval, and free ID-based Ragas metrics.
- `database_native_benchmark_2026-08-12.json`: unified-database startup, latency, and peak-memory
  baseline over 25 local requests.
- `overall_metrics_2026-08-10.json`: historical pre-Camden headline snapshot.
- `westminster_rag_holdout_*`: historical holdout result summaries.
- `westminster_local_llm_evaluation_2026-08-10.json`: local Ollama comparison.
- `camden_expansion_2026-08-10.json`: Camden import and expansion provenance.
- `*_passage_feature_review_*.json`: reviewed evidence decisions consumed by dataset builds; these
  are operational inputs and must not be removed as redundant reports.
- `westminster_top20_2026-08-08.json`: fixed venue selection used by documented enrichment steps.

Raw generated reports belong in ignored `data/local/`, not here. Add a dated audit only when the
result is important enough to preserve after its generated report is removed.
