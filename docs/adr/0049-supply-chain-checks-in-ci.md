# ADR-0049 — Supply-chain checks in CI: Dependabot, dependency audit, gitleaks, Trivy image scan and SBOM, everything pinned

- **Status:** Accepted (2026-10-31)
- **Date:** 2026-10-30
- **Deciders:** engineering (product owner to confirm)

## Context

Neither repository audited its dependencies, scanned for secrets or built its images in CI
([gap analysis](../security/gap-analysis.md) 15.1.2, 15.2.1, CC6.8, LLM04; [launch plan](../security/launch-plan.md)
S0-10; [verification plan](../security/verification-plan.md) S1). ADR-0031 §6 already names
Trivy for image scanning and Notation for signing in UMAI Cloud; the launch plan names
CycloneDX, `pip-audit` and Dependabot. What was missing is how the checks gate a change, how a
finding is accepted, and how the tools themselves are trusted.

Tools are part of the attack surface. In March 2026 Trivy's own release 0.69.4, Docker Hub images
0.69.5–0.69.6 and nearly every tag of `aquasecurity/trivy-action` and `setup-trivy` were replaced
with credential-stealing code ([GHSA-69fq-xp46-6x23](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23));
the same campaign published compromised LiteLLM releases to PyPI (ADR-0034, alternatives).
Workflows that referenced the actions by tag ran the attacker's code with their secrets.
`gitleaks-action` needs a paid licence for organization accounts.

First run on 2026-10-30, before any fix (engine `21defe1`, console `2f2e9a9`):

| Check | Engine | Console |
|---|---|---|
| Dependency audit | `pip-audit` over the ~80 installed packages (runtime + dev): none | `npm audit --omit=dev`: `next` 14.2.35 with 25 advisories (3 critical, 9 high), none fixed in 14.x; `postcss` 8.4.31 (high) |
| Image (Trivy, fixable HIGH/CRITICAL) | Debian 13.7, Python packages: none; no secrets | `node:20-alpine` (Node 20 is end of life): OpenSSL 3.5.6 (2 HIGH); npm's bundled packages (`tar`, `glob`, `minimatch`, `cross-spawn`, `sigstore`, …: 1 critical, 21 high); `next`, `postcss` as above |
| Secrets (gitleaks, all history) | 15 findings, all synthetic test vectors (jwt.io's example token, placeholder PEMs in the L0 eval dataset, a synthetic `sk-proj-` test key) or public Azure role ids | none |

## Decision

1. **Dependabot** in both repositories: `pip` / `npm`, `github-actions`, `docker` (and
   `docker-compose` for `deploy/vm`), weekly, minor and patch grouped into one pull request per
   ecosystem, majors one each, at most 2–5 open pull requests per ecosystem, and a 7-day cooldown
   so a release is a week old before it is proposed. Python minor and major, Node major and
   PostgreSQL major are ignored: those are decisions (ADR-0009, the console's Node 22 LTS, a
   dump-and-restore on the VM), not updates. Security updates stay immediate.
2. **Everything third-party in CI is pinned.** Actions by full commit SHA with the version as a
   comment (Dependabot updates both); scanners run as container images by digest (Trivy 0.76.1,
   gitleaks 8.30.1), not through their actions; Dockerfile base images by digest, moved weekly by
   Dependabot. A unit test in each repository (`tests/unit/test_supply_chain.py`,
   `tests/supply-chain.test.mjs`) fails when a pin is missing. Supply-chain jobs get no secrets
   and only `contents: read`; checkouts do not persist credentials.
3. **One `supply-chain.yml` workflow per repository**, on pull requests, on `main`, weekly
   (Monday) and on demand, three parallel jobs:
   - **dependency audit**: engine `pip-audit` (pinned version, own virtual environment) over the
     exact versions `pip install -e ".[dev]"` resolved, `--no-deps --strict`; console
     `scripts/audit-gate.mjs` over `npm audit --omit=dev --json`, failing on high and critical.
   - **secret scan**: gitleaks with its default rules over every commit reachable from the
     checked-out one. Allowlists (`.gitleaks.toml`) name exact values, never paths, each with
     the place that documents it as synthetic.
   - **image**: build the repository's image once, write a CycloneDX SBOM with Trivy and upload
     it as a workflow artifact (90 days), then scan with Trivy (`vuln,secret`, HIGH and
     CRITICAL, `--ignore-unfixed`); the scan fails the job. The vulnerability database is cached
     per day.
4. **Accepting a finding is explicit and temporary.** Image findings go to `.trivyignore.yaml`
   with `statement` and `expired_at`; console npm advisories to `.github/npm-audit-allowlist.json`
   with `reason` and `expires`. An entry names why the advisory does not apply or why it waits and
   what removes it; it expires at most 90 days ahead (checked by tests and by the gate), and an
   expired entry fails the check again. An entry that no longer matches is reported for removal.
5. **Findings of the first run:** the console runtime image moves to `node:22-alpine` and drops
   npm, npx, corepack and yarn; `postcss` is overridden to 8.5.31; Next.js's image optimizer is
   turned off (`images.unoptimized`), which takes the two critical optimizer advisories off the
   attack surface; the single-VM Caddy refuses WebSocket upgrade requests to the console
   (GHSA-c4j6-fc7j-m34r). The twelve high and critical Next.js 14 advisories are accepted until
   2026-11-30, each with its reason; upgrading the console to Next.js 15.5.28 or later removes
   them and is the next console change.
6. **Not in this decision:** image signing with Notation and pushing to ACR (ADR-0031 §6) waits
   for the registry; branch protection, required checks, Dependabot alerts and security updates,
   private vulnerability reporting and GitHub secret scanning with push protection are repository
   settings, switched on by an administrator.

## Alternatives considered

| Option | Why not |
|---|---|
| Renovate instead of Dependabot | More capable (lock-file maintenance, regex managers), but a GitHub App with write access to install and configure; Dependabot is built in, handles SHA-pinned actions and digests, and has cooldown and grouping now |
| `aquasecurity/trivy-action`, `gitleaks-action` | The Trivy actions were the vector of the March 2026 compromise; `gitleaks-action` needs a licence for organizations. The pinned images do the same with one less layer of third-party code |
| Grype + Syft (Anchore) for scan and SBOM | Equivalent; Trivy is already the scanner ADR-0031 names, and one tool gives both the SBOM and the scan from the same analysis |
| Plain `npm audit --audit-level=high` | No way to accept an advisory: the console's CI would stay red until the Next.js upgrade, or the check would be switched off. The gate keeps npm's verdict and adds dated, reasoned exceptions |
| Fail on unfixed vulnerabilities too | Debian and Alpine carry HIGH entries with no fix (`affected`, `will_not_fix`); failing on them blocks every change with nothing to do. Unfixed findings stay visible in the scheduled report and the SBOM |
| Python lock file (pip-tools / uv) for reproducible audits | The better long-term answer to "the image gets whatever pip resolves today", but it changes the build and ADR-0009; the audit runs on the resolved versions, the same ones the image build resolves |
| Upgrade the console to Next.js 15.5/16 in this change | The right fix for the accepted advisories, but a major upgrade (React 19, async request APIs) across the console while six other console changes are open; done next, separately |

## Consequences

- Every pull request in both repositories gets three more checks (about 4–5 minutes, parallel
  with the existing CI). A new advisory against unchanged code turns `main` and open pull
  requests red until it is fixed or accepted with a date; the weekly run finds it even without a
  change.
- A red image scan with a fix in a newer base image is resolved by merging Dependabot's digest
  update (or triggering it from the repository's Dependabot page).
- Dependabot opens up to about a dozen pull requests per week across both repositories; each runs
  the full CI.
- Exceptions are visible and dated: on 2026-11-30 the console's twelve Next.js entries expire and
  its supply-chain checks fail until the upgrade lands.
- SBOMs exist per CI run as artifacts, not yet per released image; they move to the registry
  with signing (ADR-0031 §6) when images are pushed to ACR.

## Validation

- Verification plan S1 green: dependency audit, SBOM and image scan run in CI for both
  repositories, and no known critical CVE ships unaccepted.
- The pinning tests fail when someone adds an action by tag or an unpinned base image.
- Revisit when images are pushed to a registry (sign and attach the SBOM there), when the
  Python build gets a lock file, or if the weekly run turns red more than once a month without a
  real finding.
