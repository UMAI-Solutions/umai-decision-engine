# ADR-0054 — Customer API documentation: a static site built from the engine repository, served at `/docs/` on the UMAI address, in English and Turkish

- **Status:** Proposed
- **Date:** 2026-10-31
- **Deciders:** engineering (product owner to confirm)

## Context

Stage 0 of the [launch plan](../security/launch-plan.md) onboards two or three design partners.
Their developers integrate against two public surfaces of this repository: the evaluation API
(`POST /v1/evaluate`, contract [evaluation-api](../contracts/evaluation-api.md) 0.14) and the AI
gateway (OpenAI-compatible, contract [gateway-api](../contracts/gateway-api.md) 0.14). Until now
the only customer-facing material was one snippet on the console's "API & SDKs" page
(`umai-control-center`). The contracts are written for engineers of this repository: they name
ADRs, settings and internal layers, and they describe routes customers must not see.

Constraints:

- **Never expose internal surfaces.** FastAPI serves `/docs` and `/openapi.json` for every route
  of an app, including `/v1/detect` (development), the bundle routes and, in the policy service,
  every control-plane route. The single VM answers those paths with 404
  ([gap analysis](../security/gap-analysis.md) 13.4.5); that must stay so.
- **Generated, not retyped.** Request and response fields, error bodies and versions must come
  from the code (the FastAPI OpenAPI schemas), so the documentation cannot drift from the API;
  AGENTS rule 5 (contracts before code) must reach the published documents too.
- **Bilingual.** The console speaks English and Turkish, and the first customers are Turkish
  (`umai-control-center` `docs/i18n.md`: Turkish is first-class).
- **Editions.** One release serves SaaS and self-hosted (ADR-0018). A self-hosted customer
  needs the documentation of the version they run, not of whatever the SaaS runs today.
- **Supply chain.** Every dependency and every published asset is surface (ADR-0049); the
  console runs a nonce-based CSP.
- Stage 0 makes no public claims about compliance; the beta is under NDA.

## Decision

1. **Source.** The documentation lives in this repository under [`docs/api/`](../api/):
   hand-written pages in `docs/api/en/` and `docs/api/tr/` (Markdown, one file per page, the same
   file names in both languages), and the published OpenAPI documents in `docs/api/openapi/`
   (`evaluation-api.json`, `gateway-api.json`), generated from the apps.
2. **Generated from the apps.** The engine API and the gateway describe their customer routes in
   code: summaries, descriptions, the UMAI headers, error bodies and examples
   (`api/openapi.py`, `gateway/openapi.py`), field descriptions on the contract models, and
   `info.version` = the contract document's version (`API_VERSION` in each app).
   `public_document()` keeps an **allow-list** of operations (`POST /v1/evaluate`; the gateway's
   `POST /v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `GET /v1/models`), removes
   properties of development-only features (`x-umai-internal`, e.g. `bundle_inline`) and prunes
   unreferenced schemas. A new route is never published by accident; publishing one is a change
   to the allow-list, reviewed like a contract change. Not published: the policy service,
   operator routes (`/v1/bundles/reload`), `/v1/detect`, the bundle read routes (console-facing),
   health routes.
3. **One check holds code, contract and documents together.** `tests/unit/test_api_docs.py`
   fails when a committed OpenAPI document differs from what the apps generate, when its
   `info.version` differs from the contract document's `**Version:**` or the contracts index,
   when a path outside the allow-list appears, or when an error code the code can emit is
   missing from either language's error catalogue. `python dev/api_docs.py openapi` rewrites the
   documents.
4. **Site.** `python dev/api_docs.py build` renders the pages and a reference generated from the
   OpenAPI documents into a static site: plain HTML and one stylesheet, relative links only, no
   JavaScript, no external assets, no timestamps (the same input gives the same bytes). The only
   new dependency is `markdown-it-py`, pinned exactly, in the `docs` and `dev` extras. CI
   (`ci.yml`, step "API documentation" on Linux and Windows) checks the OpenAPI documents, builds
   the site twice and compares the trees, and uploads the Linux build as the artifact `api-docs`.
5. **Where it is served.** At **`/docs/` on the UMAI address**, the same host as the API: English
   at `/docs/`, Turkish at `/docs/tr/`, the OpenAPI documents at `/docs/openapi/`. The edge
   proxy serves the built files; FastAPI's own `/docs`, `/redoc` and `/openapi.json` stay
   disabled at the edge. On the single VM the engine image builds the site (a `docs` stage in
   the Dockerfile), the one-shot `seed` copies it into the `docs` volume with every new image,
   and Caddy serves that volume with `handle_path /docs/*` and a `file_server`. A self-hosted
   install therefore serves the documentation of its own release. The
   console's "API & SDKs" page links to it in the person's language.
6. **Language.** English and Turkish, both complete: every hand-written page exists in both
   (a test compares the page sets and the error codes per page). The generated reference renders
   field descriptions from the OpenAPI schema, which are English, inside Turkish pages with
   Turkish headings and a note that field names and descriptions are those of the API. Code,
   field names, error codes and examples are the same in both languages. English is the source:
   a change lands in both languages in the same pull request.
7. **Examples** are in curl, Python and JavaScript, with synthetic values only (AGENTS rule 2;
   Turkish identifiers from [`turkish-identifiers.md`](../reference/turkish-identifiers.md)).
   Their JSON bodies are validated against the contract models by the same test.

## Alternatives considered

| Option | Why not |
|---|---|
| Static site hosted elsewhere (GitHub Pages, a docs domain) | Needs organization-level settings, a new origin and its DNS and TLS; a self-hosted customer would read the SaaS's version, not their own. The build is the same as the chosen option, so moving the files later is cheap |
| A section of the console's "API & SDKs" page | Behind sign-in: an integrating developer at a design partner needs a console seat to read how to call the API; the long-form text would live in TypeScript dictionaries; the console would need a copy of the OpenAPI documents (drift between two repositories) and a renderer that fits its CSP. The console stays the place for the person's own address, keys and first snippet, and links to the docs |
| FastAPI's built-in `/docs` (Swagger UI) or Redoc | Shows every route of the app, internal ones included (gap 13.4.5); loads JavaScript from a CDN; English only; no place for guides, limits or the error catalogue |
| MkDocs (Material) or another site framework | Search, themes and versioned navigation are not needed for eight pages per language; a theme, plugins and a JavaScript bundle are more supply-chain surface (ADR-0049) than ~300 lines of our own renderer on one Markdown library |
| English only, Turkish later | The first customers are Turkish and the console is already bilingual; written once while the content is small, Turkish costs little, and the error catalogue is where a Turkish developer needs it most |
| Translating the generated field descriptions too | A second copy of ~90 descriptions in a dictionary drifts silently when the English changes; the hand-written pages already explain every field a caller sets in Turkish. Revisit if a design partner asks |

## Consequences

- A contract version bump now also bumps `API_VERSION` in the app and regenerates
  `docs/api/openapi/` (the test fails otherwise); the `change-contract` skill says so.
- Publishing a route is explicit (the allow-list); removing a route from the documents is too.
- New error codes need a line in both error catalogues before CI passes.
- The single VM serves the site from the engine image (Dockerfile `docs` stage, `seed-docs.sh`,
  Caddy). The docs are public on the beta host; nothing in them is secret, and they
  make no compliance claims. If the product owner wants them closed during the NDA beta, Caddy's
  basic auth on `/docs/*` is a one-line switch.
- Turkish pages show English field descriptions in the generated reference (stated on the page).

## Validation

- `tests/unit/test_api_docs.py` passes in CI on Linux and Windows, and the CI step builds the
  site twice to the same bytes.
- A design partner integrates from the documentation alone (Stage 0 exit: partners onboarded);
  questions they ask go into the pages. Revisit the location at Stage 1 (self-serve), when the
  documentation needs search, versioning per release, or its own origin.
