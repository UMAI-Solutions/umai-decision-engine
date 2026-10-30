"""Records (ADR-0041, ADR-0042): decision events, L1b outcome records and usage records.

- :mod:`build` turns an evaluation into records (contracts tagged-action 0.3, usage 0.1): ids,
  types, counts, codes and actions only, never content;
- :mod:`recorder` is what the pipeline calls: a no-op by default, in memory for tests and the
  leak suite, or a bounded queue drained by a background writer, so a record never delays a
  decision;
- :mod:`database` is the writer for the tenant store (append-only tables under RLS).

The pipeline only emits; nothing here imports the pipeline.
"""
