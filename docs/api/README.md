# Customer API documentation (maintainers)

The documentation design partners and customers read: guides for the evaluation API and the AI
gateway, authentication, limits and every error code, with curl, Python and JavaScript
examples, in English and Turkish ([ADR-0054](../adr/0054-customer-api-documentation.md)).
This README is for maintainers and is not part of the site.

| Path | What |
|---|---|
| `en/*.md`, `tr/*.md` | The pages, one file per page, the same names in both languages. Links between pages use `page.md`; the build turns them into `.html` |
| `openapi/evaluation-api.json`, `openapi/gateway-api.json` | The published OpenAPI documents, generated from the apps; never edit by hand |
| [`../../dev/api_docs.py`](../../dev/api_docs.py) | Generator: `openapi` writes the documents, `build` renders the site with the reference pages |
| [`../../tests/unit/test_api_docs.py`](../../tests/unit/test_api_docs.py) | Holds code, contract versions, pages and examples together |

```powershell
python dev/api_docs.py openapi               # after changing a route, a contract model or API_VERSION
python dev/api_docs.py build --out site/docs # the site; serve site/ and open /docs/
```

## Changing the documentation

- **A route, field or description changed:** edit it in the code (route decorators,
  `api/openapi.py`, `gateway/openapi.py`, `Field(description=...)` on the contract models), then
  run `python dev/api_docs.py openapi`.
- **A contract version changed:** bump `API_VERSION` in `api/app.py` or `gateway/app.py` to the
  contract document's version; the test compares them (skill `change-contract`).
- **A new error code:** add a row to both `en/errors.md` and `tr/errors.md`, in the table of
  its surface. The test reads the codes the code can raise and fails on a missing row.
- **A new public route:** add it to `PUBLIC_OPERATIONS` in `api/openapi.py`. Nothing else is
  published: policy-service and operator routes, `/v1/detect`, the bundle and health routes
  stay out.
- **Text:** write English first and the Turkish page in the same change. Terms follow the
  console's glossary (`umai-control-center` `docs/i18n.md`): guardrail, AI Gateway, istem,
  denetim, kuruluş, aylık istek hakkı. Examples use synthetic values only
  ([turkish-identifiers.md](../reference/turkish-identifiers.md), `@example.com`); code blocks
  are the same in both languages, and ruff formats the Python ones.

## Serving it

The site is static: plain HTML, one stylesheet, the two OpenAPI documents, no JavaScript. CI
uploads it as the artifact `api-docs` (its root holds `docs/`). On the single VM (`deploy/vm/`):

- the engine image builds it in the Dockerfile's `docs` stage (`markdown-it-py` pinned like the
  `docs` extra; a test keeps the two pins equal) and keeps it in `/app/docs-site`;
- the one-shot `seed` service runs `seed-docs.sh` with every new image: it copies the site into
  the `docs` volume, writing new files first and removing stale ones after, so no page is missing
  while it runs;
- Caddy mounts that volume read-only at `/srv/docs` and serves it with `handle_path /docs/*`
  (`/docs` redirects to `/docs/`), with a strict Content-Security-Policy and a five-minute cache.
  FastAPI's own `/redoc` and `/openapi.json` stay closed (gap analysis 13.4.5).

`handle_path` strips the `/docs` prefix, so `/docs/tr/errors.html` is served from
`/srv/docs/tr/errors.html`.
