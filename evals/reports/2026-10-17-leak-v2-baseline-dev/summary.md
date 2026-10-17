# leak — v2-baseline-dev

- **Date:** 2026-10-17
- **Git commit:** 0f0b420 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `a2aeaa89943b`), split `dev`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Bundle:** `gr-demo-data-protection` (active version), phase PRE_LLM, destination external_llm, trace on, logs captured at DEBUG
- **Cases:** 207 with at least one entity

## Verdict: PASS

| Where | Cases with a raw value | Must be |
|---|---|---|
| Response and trace (outside the masked text) | 0 | 0 |
| Logs | 0 | 0 |

Decisions: ALLOW 87, ALLOW_WITH_MODIFICATIONS 44, BLOCK 76

## Values that would reach the destination: 87 cases

The decision allowed the content and the value was not masked, so the caller would send
it to the external LLM. Not a leak of the engine's own outputs: each one is an L0 miss
(or an entity type L0 does not support yet) with a real consequence.

- l0-0036: TR_TCKN (decision ALLOW)
- l0-0038: TR_TCKN (decision ALLOW)
- l0-0040: TR_TCKN (decision ALLOW)
- l0-0051: TR_TCKN (decision ALLOW)
- l0-0055: TR_TCKN (decision ALLOW)
- l0-0056: TR_TCKN (decision ALLOW)
- l0-0096: TR_PHONE (decision ALLOW)
- l0-0114: TR_IBAN (decision ALLOW)
- l0-0156: EMAIL_ADDRESS (decision ALLOW)
- l0-0239: TR_VKN (decision ALLOW)
- l0-0241: TR_VKN (decision ALLOW)
- l0-0242: TR_VKN (decision ALLOW)
- l0-0243: TR_VKN (decision ALLOW)
- l0-0244: US_SSN (decision ALLOW)
- l0-0245: US_SSN (decision ALLOW)
- l0-0246: US_SSN (decision ALLOW)
- l0-0247: US_SSN (decision ALLOW)
- l0-0248: US_SSN (decision ALLOW)
- l0-0249: US_SSN (decision ALLOW)
- l0-0250: US_SSN (decision ALLOW)
- l0-0256: PHONE_NUMBER (decision ALLOW)
- l0-0258: PHONE_NUMBER (decision ALLOW)
- l0-0261: PHONE_NUMBER (decision ALLOW)
- l0-0262: PHONE_NUMBER (decision ALLOW)
- l0-0263: PHONE_NUMBER (decision ALLOW)
- l0-0264: PHONE_NUMBER (decision ALLOW)
- l0-0269: PHONE_NUMBER (decision ALLOW)
- l0-0271: PHONE_NUMBER (decision ALLOW)
- l0-0272: PHONE_NUMBER (decision ALLOW)
- l0-0273: PHONE_NUMBER (decision ALLOW)
- l0-0274: PHONE_NUMBER (decision ALLOW)
- l0-0275: PHONE_NUMBER (decision ALLOW)
- l0-0276: IP_ADDRESS (decision ALLOW)
- l0-0277: IP_ADDRESS (decision ALLOW)
- l0-0278: IP_ADDRESS (decision ALLOW)
- l0-0279: IP_ADDRESS (decision ALLOW)
- l0-0280: IP_ADDRESS (decision ALLOW)
- l0-0282: IP_ADDRESS (decision ALLOW)
- l0-0291: UNKNOWN_LINK (decision ALLOW)
- l0-0292: UNKNOWN_LINK (decision ALLOW)
- l0-0293: UNKNOWN_LINK (decision ALLOW)
- l0-0294: UNKNOWN_LINK (decision ALLOW)
- l0-0295: UNKNOWN_LINK (decision ALLOW)
- l0-0300: ADDRESS (decision ALLOW)
- l0-0301: ADDRESS (decision ALLOW)
- l0-0302: ADDRESS (decision ALLOW)
- l0-0305: ADDRESS (decision ALLOW)
- l0-0306: ADDRESS (decision ALLOW)
- l0-0308: ADDRESS (decision ALLOW)
- l0-0309: ADDRESS (decision ALLOW)
- l0-0310: ADDRESS (decision ALLOW)
- l0-0311: ADDRESS (decision ALLOW)
- l0-0316: PERSON (decision ALLOW)
- l0-0317: PERSON (decision ALLOW)
- l0-0318: PERSON (decision ALLOW)
- l0-0320: PERSON (decision ALLOW)
- l0-0321: PERSON (decision ALLOW)
- l0-0322: PERSON (decision ALLOW)
- l0-0323: PERSON (decision ALLOW)
- l0-0324: PERSON (decision ALLOW)
- l0-0325: PERSON (decision ALLOW)
- l0-0326: PERSON (decision ALLOW)
- l0-0327: PERSON (decision ALLOW)
- l0-0333: ES_NIF (decision ALLOW)
- l0-0334: ES_NIF (decision ALLOW)
- l0-0336: ES_NIF (decision ALLOW)
- l0-0337: ES_NIE (decision ALLOW)
- l0-0338: ES_NIE (decision ALLOW)
- l0-0339: ES_NIE (decision ALLOW)
- l0-0340: IT_FISCAL_CODE (decision ALLOW)
- l0-0342: IT_FISCAL_CODE (decision ALLOW)
- l0-0344: NL_BSN (decision ALLOW)
- l0-0346: PL_PESEL (decision ALLOW)
- l0-0347: PL_PESEL (decision ALLOW)
- l0-0349: DE_TAX_ID (decision ALLOW)
- l0-0351: DE_TAX_ID (decision ALLOW)
- l0-0353: FR_NIR (decision ALLOW)
- l0-0354: FR_NIR (decision ALLOW)
- l0-0355: UK_NINO (decision ALLOW)
- l0-0357: UK_NINO (decision ALLOW)
- l0-0359: FR_NIR (decision ALLOW)
- l0-0363: TR_PLATE (decision ALLOW)
- l0-0364: TR_PLATE (decision ALLOW)
- l0-0369: EMAIL_ADDRESS (decision ALLOW)
- l0-0370: EMAIL_ADDRESS (decision ALLOW)
- l0-0371: CREDIT_CARD (decision ALLOW)
- l0-0372: CREDIT_CARD (decision ALLOW)
