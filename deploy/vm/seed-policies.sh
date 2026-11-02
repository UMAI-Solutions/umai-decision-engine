#!/bin/sh
# Copy the image's seed bundles into the policy volume and sign every version with this
# deployment's key (ADR-0032). Only bundles the volume does not have yet are copied: an empty
# volume gets them all, a running one gets what a later release added (the guardrail templates
# of ADR-0056 §3). A bundle already in the volume is never touched: the policy service owns it
# (ADR-0022). Each bundle is signed in a hidden staging directory, which the engine ignores,
# and moved into place whole.
set -eu

target=${UMAI_SEED_TARGET:-/data/policies}  # the variables exist for tests
seed=${UMAI_SEED_SOURCE:-/app/seed-policies}
key=/run/secrets/signing.key

added=0
for bundle in "$seed"/*/; do
	name=$(basename "$bundle")
	[ -e "$target/$name" ] && continue
	staging="$target/.seed-$name"
	rm -rf "$staging"
	cp -R "$bundle" "$staging"
	for version in "$staging"/*/; do
		[ -f "${version}bundle.yaml" ] || continue
		python -m umai_decision_engine.policy.cli sign "$version" \
			--key-id "$UMAI_SIGNING_KEY_ID" --private-key-file "$key"
	done
	mv "$staging" "$target/$name"
	echo "seed: added $name"
	added=$((added + 1))
done
echo "seed: done ($added added)"
