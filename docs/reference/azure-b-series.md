# Azure B-series (Bv1) VMs: what a Standard_B2s can sustain

**Last verified:** 2026-10-31 · **Used by:** the `load` eval suite's capacity estimate (launch plan
S0-3), production VM sizing (ADR-0032 starter, umai-beta runs a B2s) · Labels: see
[README](README.md). Facts are **vendor claims** from Microsoft Learn unless marked otherwise.

## Source keys

| Key | Page |
|---|---|
| BV1 | [Bv1 size series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/bv1-series) (updated 2026-09-27) |
| CM | [B Series CPU credit model](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/b-series-cpu-credit-model) (updated 2026-02-19) |

## Standard_B2s

| Fact | Value | Source |
|---|---|---|
| vCPUs, memory | 2, 4 GiB | BV1 |
| Base CPU performance of the VM | 20 % (2024 numbers on a 0–100 % scale for the whole VM; earlier pages used 0–vCPU × 100 %, i.e. 40 %) | BV1 |
| Initial credits, credits banked per hour, max banked | 60, 24, 576 | BV1 |
| Processors | Ice Lake 8370C, Cascade Lake 8272CL, Skylake 8171M 2.1 GHz, Broadwell E5-2673 v4 2.3 GHz or Haswell E5-2673 v3 2.4 GHz: "various hardware types"; query the VM to know which | BV1 |
| Lifecycle | the Bv1 series is in the **End of Life** stage with an announced retirement; new deployments should use current sizes | BV1 |
| Out of credits | throttled back to the base CPU performance until credits accumulate again | CM |

**Credit arithmetic** (CM formula: credits banked per minute = (base % × vCPUs − CPU % × vCPUs) / 100):

- Sustained without credits: 20 % of 2 vCPUs = **0.4 vCPU** for everything on the VM.
- At 100 % on both vCPUs: (20 × 2 − 100 × 2) / 100 = −1.6 credits per minute; a full bank of 576
  credits lasts **6 hours** of full burst (our arithmetic). Idle, the VM banks 0.4 per minute
  (24 per hour, matching BV1).
- One credit is therefore one vCPU-minute at 100 % (our reading of the formula; CM does not say it
  in those words).

## What this means for UMAI (not vendor facts)

- Bursts are free while credits last: a few hours of saturation a day are affordable, sustained
  load is not. Capacity planning for a B2s uses the 0.4 vCPU figure; the burst figure is the
  ceiling for short peaks. Credits remaining are an Azure Monitor metric (CM): an alert on it is
  the early warning.
- The CPU model is unknown until the VM is asked; per-request CPU measured on a laptop scales by a
  factor that differs between Haswell and Ice Lake hosts. The `load` suite's estimate states a
  range; the beta plan (`docs/workflow/load-test-beta.md`) narrows it on the VM itself.
