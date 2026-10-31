# Load test against umai-beta (plan, launch plan S0-3)

**Status:** 2026-10-31, plan only. Nothing here has been run against `umai-beta`. Running it needs
the product owner's explicit go-ahead for the date and window, because the design partner's
traffic shares the VM.

The local measurement (`evals/reports/2026-10-31-load-s0-3-laptop-2cpu*`) gives CPU time per
request on a laptop core pinned like a B2s; the B2s estimate in it carries an unknown CPU factor
(Azure places a B2s on Haswell to Ice Lake hosts, `docs/reference/azure-b-series.md`). This plan
measures that factor and the real path (Caddy TLS, the records writer and Postgres, the console
and the policy service on the same two vCPUs) without touching a customer's quota.

## 1. What it answers

1. The CPU factor between the laptop and the VM (step 2, no traffic).
2. Throughput and p50/p95/p99 of `POST /v1/evaluate` (L0 only) and, with stubs, of
   `/v1/evaluate` with L1b and the gateway, at fixed arrival rates, through Caddy.
3. How fast CPU credits drain at each rate, so the production VM (ADR-0050) is sized on
   sustained, not burst, capacity.

## 2. Guardrails for the run

- **A dedicated test tenant**, never a customer's. Create the organization "UMAI load test"
  (`loadtest`) in the console (ADR-0033): it gets the starter guardrail
  `gr-demo-data-protection` and an application `load`. Its quota is lifted with an operator plan
  override, so the run counts against no one's month (quotas are per tenant, ADR-0043):

  ```bash
  # on the VM, inside the Compose network (the policy service port is not published)
  curl -sS -X PUT -H "Authorization: Bearer $CONTROL" -H 'content-type: application/json' \
    http://policy:9200/v1/tenants/loadtest/plan \
    -d '{"edition":"community","overrides":{"monthly_requests":5000000}}'
  ```

- **Keys:** the per-key rate limit is 20 requests/s (deployment setting, ADR-0023 §7). Mint
  `ceil(peak rate / 15)` live keys of the test tenant in the console (Applications → load) and
  pass them comma-separated in `UMAI_LOAD_API_KEYS`; the generator spreads requests over them.
  Each key gets `allowed_ips` = the generator's egress address (ADR-0046), so a leaked key is
  useless elsewhere.
- **Windows:** outside the design partner's working hours (Türkiye, UTC+3), announced to them a
  day ahead; one operator watches.
- **Abort** (Ctrl-C stops the generator at once) when any holds: p95 above 2 s for a step;
  non-2xx above 1 % (other than the test's own 429s); `CPU Credits Remaining` below 150 (Azure
  Monitor, VM metrics); the partner reports a problem. Start with credits above 400.
- **Synthetic text only** (the generator's Turkish business sentences); nothing from a customer.
- **No deploy change** for steps 1–4. Step 5 (stubs) changes configuration and needs the ops owner.

## 3. Steps

1. **Record the starting point:** `umai.sh status`, `docker stats --no-stream`, CPU credits
   remaining, the engine and gateway image tags, `lscpu | grep 'Model name'` (which host CPU).
2. **CPU factor, no traffic:** run the in-process suite in a one-off container of the engine
   image with this repository's checkout mounted, as the 2026-10-26 `l1-cold-start` run did (it
   reads no customer data, calls nothing and needs no secret):

   ```bash
   mkdir -p /tmp/umai-reports && chmod 777 /tmp/umai-reports
   docker run --rm -w /app -v /opt/umai/src/umai-decision-engine:/app:ro \
     -v /tmp/umai-reports:/app/evals/reports umai-decision-engine:current \
     python -m evals.run --suite l0-latency --cpu none --tag beta-b2s
   ```

   The ratio of its numbers to `2026-10-31-l0-latency-s0-15-laptop` is the CPU factor; the
   admission rows also close S0-15 on the VM itself.
3. **Generator placement:** a small VM in West Europe (same region, no internet latency), or the
   workstation with open-loop rates (closed loops from Türkiye would measure the round trip).
   Its address goes into the NSG and the keys' `allowed_ips`.
4. **L0 only, open loop:** steps of 2, 5, 10, 20, 40 requests/s, 60 s each:

   ```bash
   UMAI_LOAD_API_KEYS=<k1,k2,k3> python -m evals.run --suite load --scenarios evaluate-l0 \
     --engine-url https://<beta-host> --rates 2,5,10,20,40 --duration-s 60 --warmup-s 5 \
     --bundle-l0 gr-demo-data-protection --tag beta-b2s-l0
   ```

   Between steps, note `docker stats` for engine, policy, postgres, caddy and the credit drain.
5. **L1b and the gateway (optional, ops owner):** a stub container (`uvicorn evals.stubs:app`) on
   the Compose network for the run only, a `stub` entry in `UMAI_DE_DECISION_PROVIDERS` and its
   address in the provider allow-list (`routes.env`), a guardrail `gr-load-l1b` for the test
   tenant (the demo's L1b version, two policies enforcing, provider `stub`), and the application
   `load` pointed at a provider with `base_url` = the stub. Then the same command with
   `--scenarios evaluate-l1b,gateway --gateway-url https://<beta-host> --bundle-l1b gr-load-l1b`
   and `UMAI_LOAD_PROVIDER_KEY` set to any placeholder. Revert all three settings afterwards.
   Without step 5, L1b and gateway capacity on the VM are the local CPU times × the step-2 factor.
6. **Clean up:** revoke the test keys, remove the plan override, leave the tenant (its records
   expire with retention: decisions 30 days, usage 400 days), write the report under
   `evals/reports/<date>-load-beta-b2s-*` and update the launch plan's S0-3 row.

## 4. What the run must not do

- Use a customer's tenant, key or guardrail, or a real provider key (L1b runs only against the
  stub; the real OpenAI provider stays the partner's).
- Change rate limits, plans files or secrets on the VM (step 5's three settings are the only
  configuration change, and only with the ops owner).
- Run without the abort watch, or longer than the window.
