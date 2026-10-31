#!/bin/sh
# Copy the customer API documentation of this image into the docs volume Caddy serves at /docs/
# (ADR-0054). Runs with every new image (the seed is a one-shot), so the site always matches the
# running release. New files are written first and stale ones removed after, so a page never
# disappears while Caddy serves it.
set -eu

target=/data/docs
site=/app/docs-site

cp -R "$site"/. "$target"/
(cd "$target" && find . -type f) | while read -r file; do
	[ -e "$site/$file" ] || rm -f "$target/$file"
done
find "$target" -mindepth 1 -type d -empty -delete
echo "seed: docs $(find "$target" -type f | wc -l) files"
