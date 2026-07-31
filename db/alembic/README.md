# Database migration history

`db/schema_postgresql.sql` is the desired-schema source of truth. The initial
Alembic revision uses the frozen
`db/alembic/baselines/20260731_schema.sql` asset so historical upgrades remain
reproducible even after the canonical schema changes.

`db/alembic/manifests/20260731_schema_manifest.json` is the immutable
PostgreSQL catalog manifest generated from that baseline. Legacy adoption
compares the complete structure, not a representative subset. Migration 05 is
the only compatibility exception: its three nullable `market_index_daily`
date/timestamp columns are treated as canonical NOT NULL columns only when all
three exact `NOT VALID` not-null guard constraints are present; its exact
null-aware source-date check is then normalized to the canonical check.
Physical column ordinals are excluded because the application addresses
columns by name; all other column metadata remains part of the strict
comparison.

The baseline squashes these legacy SQL files. They are an immutable archive and
must not be edited or replayed against a database adopted at the baseline:

| Legacy migration | SHA-256 |
| --- | --- |
| `20260728_01_batch_job_trigger_subject_text.sql` | `2695d3b9aa7136898ebc2934ff19a77c15bc49bdedeb0dacba5ca9b32863b734` |
| `20260728_02_naver_news_keyword_seeds.sql` | `0696f86e9c10429fba16d46f35059101e23b40eca8ed0bb18a5da3576d66dd8f` |
| `20260728_03_news_article_processed_date_dedupe.sql` | `740ce151b46c768effb5fc9c4adf4ba2bf66e00c867eb2e8f4bfc1a299d2d584` |
| `20260729_04_batch_job_durable_queue.sql` | `ea57c65035be21f7ff48fcff0c0a4c5c6da863cfd5b4279f4f7eedc0cdef787a` |
| `20260729_05_market_session_context_source_date.sql` | `05b6fe58ae55aa456fd97e7b781b5cfd881591a99726226a0095a69adcf06a54` |
| `20260729_06_ai_summary_retry_lineage.sql` | `9d17cdaad60630e0f71a814c3bc66ad9832b8cff54ba0f59207b26e0bf640a56` |
| `20260731_07_incremental_news_collection.sql` | `74aaee3ab1e517a2cdef4f5b2d4b0f25e8895da6fa9c7d7e186ae51918cc8ebf` |

For every future schema change:

1. Add a new forward Alembic revision under `alembic/versions/`.
2. Apply the same desired state to `db/schema_postgresql.sql`.
3. Do not modify an existing revision or frozen baseline asset.
4. Test both an upgrade from the previous revision and a fresh database.

Automatic startup migration supports only the canonical `stock` schema.
