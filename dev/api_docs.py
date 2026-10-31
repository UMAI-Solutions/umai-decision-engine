"""Customer API documentation (ADR-0054): the published OpenAPI documents and the static site.

    python dev/api_docs.py openapi            # write docs/api/openapi/*.json from the apps
    python dev/api_docs.py openapi --check    # fail if they are stale (CI runs the same test)
    python dev/api_docs.py build --out site   # render docs/api into a static site

The site is plain HTML with one stylesheet: relative links, no JavaScript, no external assets,
no timestamps, so the same input gives the same bytes on every machine. Pages are Markdown in
``docs/api/<language>/``; the reference pages are generated from the OpenAPI documents.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "api"
OPENAPI_DIR = SOURCE / "openapi"
DOCUMENTS = ("evaluation-api", "gateway-api")
LANGUAGES = ("en", "tr")

# Navigation order. Reference pages have no Markdown source: they are rendered from OpenAPI.
PAGES = ("index", "authentication", "evaluate", "gateway", "limits", "errors")
REFERENCE = {"reference-evaluate": "evaluation-api", "reference-gateway": "gateway-api"}

UI = {
    "en": {
        "site": "UMAI API documentation",
        "other": "Türkçe",
        "nav": "Pages",
        "reference": "Reference",
        "reference-evaluate": "Evaluation API reference",
        "reference-gateway": "AI gateway reference",
        "versions": "Contract versions",
        "evaluation-api": "Evaluation API",
        "gateway-api": "AI gateway",
        "download": "Download the OpenAPI document",
        "note": "Generated from the API's OpenAPI document.",
        "field": "Field",
        "type": "Type",
        "required": "Required",
        "description": "Description",
        "yes": "yes",
        "no": "no",
        "header": "Header",
        "headers": "Request headers",
        "response_headers": "Response headers",
        "body": "Request body",
        "responses": "Responses",
        "status": "Status",
        "examples": "Examples",
        "schemas": "Error bodies",
        "auth": "Authentication: `Authorization: Bearer <UMAI key>`.",
        "more": "Fields not listed here are your provider's: they are forwarded unchanged.",
        "or": "or",
        "array": "array of",
        "skip": "Skip to content",
    },
    "tr": {
        "site": "UMAI API belgeleri",
        "other": "English",
        "nav": "Sayfalar",
        "reference": "Başvuru",
        "reference-evaluate": "Değerlendirme API başvurusu",
        "reference-gateway": "AI Gateway başvurusu",
        "versions": "Sözleşme sürümleri",
        "evaluation-api": "Değerlendirme API",
        "gateway-api": "AI Gateway",
        "download": "OpenAPI belgesini indirin",
        "note": "API'nin OpenAPI belgesinden üretilir. Alan adları, açıklamaları ve örnekler "
        "API şemasındaki gibidir (İngilizce); her alan Türkçe olarak rehber sayfalarında "
        "anlatılır.",
        "field": "Alan",
        "type": "Tür",
        "required": "Zorunlu",
        "description": "Açıklama",
        "yes": "evet",
        "no": "hayır",
        "header": "Başlık",
        "headers": "İstek başlıkları",
        "response_headers": "Yanıt başlıkları",
        "body": "İstek gövdesi",
        "responses": "Yanıtlar",
        "status": "Durum",
        "examples": "Örnekler",
        "schemas": "Hata gövdeleri",
        "auth": "Kimlik doğrulama: `Authorization: Bearer <UMAI anahtarı>`.",
        "more": "Burada listelenmeyen alanlar sağlayıcınızındır: değiştirilmeden iletilir.",
        "or": "veya",
        "array": "dizi:",
        "skip": "İçeriğe geç",
    },
}


class BuildError(Exception):
    """The site cannot be built as written (a missing page, a broken link)."""


# ------------------------------------------------------------------ OpenAPI


def generate_openapi() -> dict[str, dict[str, Any]]:
    """The published documents, from the apps as they are in this checkout."""

    from umai_decision_engine.api.app import create_app
    from umai_decision_engine.api.openapi import public_document
    from umai_decision_engine.gateway.app import create_gateway_app

    return {
        "evaluation-api": public_document(create_app().openapi(), "evaluation-api"),
        "gateway-api": public_document(create_gateway_app().openapi(), "gateway-api"),
    }


def render_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write_openapi(check: bool) -> int:
    stale = []
    for name, document in generate_openapi().items():
        path = OPENAPI_DIR / f"{name}.json"
        text = render_json(document)
        current = path.read_bytes().decode("utf-8") if path.exists() else None
        if current == text:
            continue
        if check:
            stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
    if stale:
        print("stale (run python dev/api_docs.py openapi): " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


def load_openapi() -> dict[str, dict[str, Any]]:
    return {
        name: json.loads((OPENAPI_DIR / f"{name}.json").read_bytes().decode("utf-8"))
        for name in DOCUMENTS
    }


# ------------------------------------------------------------------ Markdown


_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "ğ": "g", "ç": "c", "ö": "o", "ü": "u"})


def slugify(text: str) -> str:
    """Heading anchors: ASCII, lower case, hyphens (Turkish letters folded)."""

    text = unicodedata.normalize("NFKD", text.translate(_FOLD).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "section"


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])

    def heading_ids(state: Any) -> None:
        seen: dict[str, int] = {}
        tokens = state.tokens
        for i, token in enumerate(tokens):
            if token.type != "heading_open":
                continue
            base = slugify(tokens[i + 1].content)
            n = seen.get(base, 0)
            seen[base] = n + 1
            token.attrSet("id", base if n == 0 else f"{base}-{n}")

    def page_links(state: Any) -> None:
        # Pages link to each other as ``page.md`` (so they read on GitHub); the site has .html.
        for token in state.tokens:
            for child in token.children or ():
                if child.type == "link_open":
                    href = child.attrGet("href") or ""
                    parts = urlsplit(href)
                    if not parts.scheme and parts.path.endswith(".md"):
                        fragment = f"#{parts.fragment}" if parts.fragment else ""
                        child.attrSet("href", parts.path[:-3] + ".html" + fragment)

    md.core.ruler.push("heading_ids", heading_ids)
    md.core.ruler.push("page_links", page_links)
    return md


MD = _markdown()


@dataclass
class Page:
    slug: str
    title: str
    body: str  # HTML


def _title(source: str, slug: str) -> str:
    match = re.search(r"^# (.+)$", source, re.M)
    if not match:
        raise BuildError(f"{slug}: the page needs a '# ' title")
    return match.group(1).strip()


# ------------------------------------------------------------------ reference pages


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _type(schema: dict[str, Any], schemas: dict[str, Any], ui: dict[str, str]) -> str:
    if "$ref" in schema:
        target = schemas.get(_ref_name(schema["$ref"]), {})
        if "enum" in target:
            return ", ".join(f"`{v}`" for v in target["enum"])
        return f"`{_ref_name(schema['$ref'])}`"
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        text = f" {ui['or']} ".join(_type(s, schemas, ui) for s in options)
        return text + (f" {ui['or']} `null`" if len(options) < len(schema["anyOf"]) else "")
    if "enum" in schema:
        return ", ".join(f"`{v}`" for v in schema["enum"])
    if "const" in schema:
        return f"`{schema['const']}`"
    kind = schema.get("type")
    if kind == "array":
        return f"{ui['array']} {_type(schema.get('items', {}), schemas, ui)}"
    if kind == "string" and schema.get("format"):
        return f"string ({schema['format']})"
    return kind or "any"


def _object(schema: dict[str, Any], schemas: dict[str, Any]) -> dict[str, Any] | None:
    """The object schema a property leads to (through a $ref, an optional or an array)."""

    if "$ref" in schema:
        target = schemas.get(_ref_name(schema["$ref"]), {})
        return target if "properties" in target else None
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            found = _object(option, schemas)
            if found is not None:
                return found
        return None
    if schema.get("type") == "array":
        return _object(schema.get("items", {}), schemas)
    return schema if "properties" in schema else None


def _rows(
    schema: dict[str, Any], schemas: dict[str, Any], ui: dict[str, str], prefix: str = ""
) -> list[str]:
    rows: list[str] = []
    required = set(schema.get("required", ()))
    for name, prop in schema.get("properties", {}).items():
        path = f"{prefix}{name}"
        is_array = prop.get("type") == "array" or any(
            o.get("type") == "array" for o in prop.get("anyOf", ())
        )
        nested = _object(prop, schemas)
        description = prop.get("description") or (nested or {}).get("description", "")
        rows.append(
            f"| `{path}` | {_cell(_type(prop, schemas, ui))} | "
            f"{ui['yes'] if name in required else ui['no']} | {_cell(description)} |"
        )
        if nested is not None and nested is not schema:
            rows.extend(_rows(nested, schemas, ui, f"{path}{'[]' if is_array else ''}."))
    return rows


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _fields(schema: dict[str, Any], schemas: dict[str, Any], ui: dict[str, str]) -> str:
    target = _object(schema, schemas) or schema
    rows = _rows(target, schemas, ui)
    if not rows:
        return ""
    head = f"| {ui['field']} | {ui['type']} | {ui['required']} | {ui['description']} |"
    return "\n".join([head, "|---|---|---|---|", *rows]) + "\n"


def _examples(content: dict[str, Any], ui: dict[str, str]) -> list[str]:
    out: list[str] = []
    for media in content.values():
        named = media.get("examples", {})
        if "example" in media:
            named = {"": {"value": media["example"]}}
        for example in named.values():
            if example.get("summary"):
                out.append(f"*{example['summary']}*\n")
            value = json.dumps(example["value"], indent=2, ensure_ascii=False)
            out.append(f"```json\n{value}\n```\n")
    return out


def reference_markdown(document: dict[str, Any], language: str) -> str:
    ui = UI[language]
    schemas = document.get("components", {}).get("schemas", {})
    name = next(k for k, v in REFERENCE.items() if v == document["x-umai-document"])
    lines = [f"# {ui[name]}\n", f"{document['info']['title']} {document['info']['version']}\n"]
    lines.append(f"{ui['note']} [{ui['download']}](openapi/{document['x-umai-document']}.json)\n")
    lines.append(f"{ui['auth']}\n")
    for path, operations in document["paths"].items():
        for method, op in operations.items():
            lines.append(f"## {method.upper()} {path}\n")
            lines.append(f"**{op.get('summary', '')}**\n")
            lines.append(op.get("description", "") + "\n")
            params = [p for p in op.get("parameters", []) if p.get("in") == "header"]
            if params:
                lines.append(f"### {ui['headers']}\n")
                lines.append(f"| {ui['header']} | {ui['description']} |\n|---|---|")
                lines.extend(
                    f"| `{p['name']}` | {_cell(p.get('description', ''))} |" for p in params
                )
                lines.append("")
            body = op.get("requestBody")
            if body:
                lines.append(f"### {ui['body']}\n")
                media = body.get("content", {}).get("application/json", {})
                lines.append(_fields(media.get("schema", {}), schemas, ui))
                if media.get("schema", {}).get("additionalProperties"):
                    lines.append(f"{ui['more']}\n")
                examples = _examples(body.get("content", {}), ui)
                if examples:
                    lines.append(f"#### {ui['examples']}\n")
                    lines.extend(examples)
            lines.append(f"### {ui['responses']}\n")
            lines.append(f"| {ui['status']} | {ui['description']} |\n|---|---|")
            for status, response in op.get("responses", {}).items():
                lines.append(f"| {status} | {_cell(response.get('description', ''))} |")
            lines.append("")
            ok = op.get("responses", {}).get("200", {})
            headers = ok.get("headers", {})
            if headers:
                lines.append(f"#### {ui['response_headers']}\n")
                lines.append(f"| {ui['header']} | {ui['description']} |\n|---|---|")
                for header, spec in headers.items():
                    lines.append(f"| `{header}` | {_cell(spec.get('description', ''))} |")
                lines.append("")
            json_ok = ok.get("content", {}).get("application/json", {})
            if "$ref" in json_ok.get("schema", {}):
                lines.append(f"#### 200: `{_ref_name(json_ok['schema']['$ref'])}`\n")
                lines.append(_fields(json_ok["schema"], schemas, ui))
            examples = _examples(ok.get("content", {}), ui)
            if examples:
                lines.append(f"#### {ui['examples']}\n")
                lines.extend(examples)
    error_bodies = [n for n in schemas if n.endswith("Body")]
    if error_bodies:
        lines.append(f"## {ui['schemas']}\n")
        for body_name in error_bodies:
            lines.append(f"### `{body_name}`\n")
            lines.append(_cell(schemas[body_name].get("description", "")) + "\n")
            lines.append(_fields({"$ref": f"#/components/schemas/{body_name}"}, schemas, ui))
    return "\n".join(lines)


# ------------------------------------------------------------------ the site


STYLE = """\
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--code:#f6f8fa;--link:#0b57d0;
--accent:#0f766e}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;
--line:#30363d;--code:#161b22;--link:#6cb6ff;--accent:#2dd4bf}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}
a{color:var(--link)}
.skip{position:absolute;left:-999px}.skip:focus{left:16px;top:8px;background:var(--bg);padding:4px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:12px 16px;
border-bottom:1px solid var(--line)}
header .site{font-weight:600;color:var(--fg);text-decoration:none}
.layout{display:flex;max-width:1200px;margin:0 auto}
nav{flex:0 0 240px;padding:16px;border-right:1px solid var(--line)}
nav h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
margin:16px 0 4px}
nav ul{list-style:none;margin:0;padding:0}
nav li a{display:block;padding:4px 8px;border-radius:6px;text-decoration:none;color:var(--fg)}
nav li a[aria-current=page]{background:var(--code);font-weight:600;color:var(--accent)}
main{flex:1;min-width:0;padding:16px 32px 64px}
h1{font-size:30px;line-height:1.25}h2{margin-top:40px;padding-bottom:4px;
border-bottom:1px solid var(--line)}
code{font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);
padding:1px 4px;border-radius:4px}
pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto}
pre code{padding:0;background:none}
table{border-collapse:collapse;display:block;overflow-x:auto;margin:16px 0}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--code)}
td code{overflow-wrap:anywhere}
blockquote{margin:16px 0;padding:4px 16px;border-left:4px solid var(--accent);color:var(--muted)}
footer{border-top:1px solid var(--line);padding:16px;color:var(--muted);font-size:14px;
text-align:center}
@media (max-width:800px){.layout{display:block}nav{border-right:none;
border-bottom:1px solid var(--line)}main{padding:16px}}
"""


def _prefix(language: str) -> str:
    return "" if language == "en" else "../"


def _href(language: str, target_language: str, slug: str) -> str:
    """A relative link from a page of ``language`` to ``slug`` in ``target_language``."""

    up = _prefix(language)
    folder = "" if target_language == "en" else "tr/"
    return f"{up}{folder}{slug}.html"


def _shell(
    page: Page, language: str, pages: list[Page], versions: dict[str, str], nav_titles: dict
) -> str:
    ui = UI[language]
    other = "tr" if language == "en" else "en"
    up = _prefix(language)

    def nav_items(slugs: list[str]) -> str:
        items = []
        for slug in slugs:
            current = ' aria-current="page"' if slug == page.slug else ""
            href = f"{slug}.html"
            items.append(f'<li><a href="{href}"{current}>{html.escape(nav_titles[slug])}</a></li>')
        return "<ul>" + "".join(items) + "</ul>"

    guides = [p.slug for p in pages if p.slug not in REFERENCE]
    refs = [p.slug for p in pages if p.slug in REFERENCE]
    version_text = " · ".join(f"{ui[name]} {version}" for name, version in versions.items())
    return (
        "<!doctype html>\n"
        f'<html lang="{language}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(_page_title(page.title, ui['site']))}</title>\n"
        f'<link rel="stylesheet" href="{up}assets/style.css">\n'
        f'<link rel="alternate" hreflang="{other}" href="{_href(language, other, page.slug)}">\n'
        "</head>\n<body>\n"
        f'<a class="skip" href="#content">{html.escape(ui["skip"])}</a>\n'
        f'<header><a class="site" href="index.html">{html.escape(ui["site"])}</a>'
        f'<a href="{_href(language, other, page.slug)}" hreflang="{other}" lang="{other}">'
        f"{html.escape(ui['other'])}</a></header>\n"
        '<div class="layout">\n'
        f'<nav aria-label="{html.escape(ui["nav"])}"><h2>{html.escape(ui["nav"])}</h2>'
        f"{nav_items(guides)}<h2>{html.escape(ui['reference'])}</h2>{nav_items(refs)}</nav>\n"
        f'<main id="content">\n{page.body}</main>\n</div>\n'
        f"<footer>{html.escape(ui['versions'])}: {html.escape(version_text)}</footer>\n"
        "</body>\n</html>\n"
    )


def _page_title(title: str, site: str) -> str:
    return title if title == site else f"{title} · {site}"


_LINK = re.compile(r'(?:href|src)="([^"]*)"')
_ID = re.compile(r'id="([^"]*)"')


def _check_links(files: dict[str, str]) -> None:
    """Every relative link resolves to a built file and, with a fragment, to an id in it."""

    ids = {path: set(_ID.findall(text)) for path, text in files.items() if path.endswith(".html")}
    problems = []
    for path, text in files.items():
        if not path.endswith(".html"):
            continue
        folder = Path(path).parent
        for href in _LINK.findall(text):
            parts = urlsplit(html.unescape(href))
            if parts.scheme or parts.netloc:
                if parts.scheme not in ("https", "mailto"):
                    problems.append(f"{path}: {href}")
                continue
            target = path if not parts.path else (folder / parts.path).as_posix()
            target = _normalise(target)
            if target not in files:
                problems.append(f"{path}: {href} (no such file)")
            elif parts.fragment and parts.fragment not in ids.get(target, set()):
                problems.append(f"{path}: {href} (no such anchor)")
    if problems:
        raise BuildError("broken links:\n  " + "\n  ".join(problems))


def _normalise(path: str) -> str:
    out: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part)
    return "/".join(out)


def site_files() -> dict[str, str]:
    """Every file of the site, path → text."""

    documents = load_openapi()
    versions = {name: documents[name]["info"]["version"] for name in DOCUMENTS}
    files: dict[str, str] = {"assets/style.css": STYLE}
    for name, document in documents.items():
        files[f"openapi/{name}.json"] = render_json(document)
    for language in LANGUAGES:
        folder = SOURCE / language
        found = sorted(p.stem for p in folder.glob("*.md"))
        if sorted(PAGES) != found:
            raise BuildError(f"{language}: pages {found}, expected {sorted(PAGES)}")
        pages: list[Page] = []
        for slug in PAGES:
            source = (folder / f"{slug}.md").read_bytes().decode("utf-8")
            pages.append(Page(slug, _title(source, slug), MD.render(source)))
        for slug, name in REFERENCE.items():
            document = {**documents[name], "x-umai-document": name}
            source = reference_markdown(document, language)
            if language != "en":
                source = source.replace("](openapi/", "](../openapi/")
            pages.append(Page(slug, UI[language][slug], MD.render(source)))
        titles = {p.slug: p.title for p in pages}
        for page in pages:
            path = f"{'' if language == 'en' else language + '/'}{page.slug}.html"
            files[path] = _shell(page, language, pages, versions, titles)
    _check_links(files)
    return files


def build_site(out: Path) -> list[Path]:
    written = []
    for relative, text in sorted(site_files().items()):
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    openapi = commands.add_parser("openapi", help="write docs/api/openapi/*.json")
    openapi.add_argument("--check", action="store_true", help="fail if the files are stale")
    build = commands.add_parser("build", help="render the static site")
    build.add_argument("--out", type=Path, default=ROOT / "site")
    args = parser.parse_args(argv)
    if args.command == "openapi":
        return write_openapi(args.check)
    try:
        written = build_site(args.out)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"built {len(written)} files in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
