#!/bin/sh
# Fill an empty policy volume from the image's seed bundles and sign every version with this
# deployment's key (ADR-0032). A volume that already holds bundles is left untouched: from then
# on the policy service owns it (ADR-0022).
set -eu

target=/data/policies
seed=/app/seed-policies
key=/run/secrets/signing.key

if [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
	echo "seed: policy volume not empty, nothing to do"
	exit 0
fi

cp -R "$seed"/. "$target"/
for bundle in "$target"/*/; do
	for version in "$bundle"*/; do
		[ -f "${version}bundle.yaml" ] || continue
		python -m umai_decision_engine.policy.cli sign "$version" \
			--key-id "$UMAI_SIGNING_KEY_ID" --private-key-file "$key"
	done
done
echo "seed: done"
