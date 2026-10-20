# Glossary

Team discussions and the wider UMAI documentation are often in Turkish; this repository
is in English. Use the English term in code and docs.

| English (use in code/docs) | Turkish | Meaning |
|---|---|---|
| Layer L0 / entity detection | L0 / varlık tespiti | Finds sensitive entities in content. |
| L0 gate | L0 kapısı | Decision made when L0 detected something: per-type `on_detect` action + deterministic exceptions; L1 skipped (ADR-0014). |
| L0 exception | L0 istisnası | Deterministic rule that relaxes the L0 gate for listed types (e.g. KYC → internal app). No model call. |
| Layer L1a / rules | L1a / deterministik kurallar | Deterministic policy rules over structured facts. |
| Layer L1b / decision model | L1b / karar modeli | Model that selects from policy-defined answers; on-prem open-weight by default, hosted Jev / OpenAI Decisions as cloud options. |
| Layer L2 / LLM review | L2 / LLM incelemesi | Asynchronous worker that reviews selected decision events with session history; produces analyses and finding candidates; never changes decisions. |
| Decision model | Karar modeli | A model class that returns typed answers to developer-defined questions instead of free text. |
| Policy bundle | Guardrail snapshot | Versioned configuration the engine evaluates against. `umai-engine` calls it `GuardrailSnapshot`. |
| Guardrail | Guardrail | Customer-facing policy set; one bundle version per guardrail version. |
| Phase | Faz | Point in the AI interaction being evaluated (`PRE_LLM`, `TOOL_INPUT`, …). |
| Mode `ENFORCE` / `MONITOR` | Uygula / izle modu | `MONITOR` always allows and only records. |
| Tag | Etiket | Machine-readable label attached to an action (`data.pii.tr_tckn`). |
| Tagged action | Etiketli eylem | L1 output record; input to L2 and to session history. |
| Decision event | Karar olayı | The tagged action emitted for every evaluation. |
| Review selection | İnceleme seçimi | `review_when` rules choosing which events L2 reviews. |
| Review rate | İnceleme oranı | Share of events queued for L2. Primary L2 cost metric. |
| Runtime path | Runtime yolu | L0 + L1; the only latency the caller sees. |
| Analysis | Analiz | L2's stored output for one reviewed event. |
| Finding candidate | Bulgu adayı | Analysis above threshold, for operator review. |
| Redaction | Maskeleme / redaksiyon | Replacing entity values with placeholders such as `<TR_TCKN_1>`. |
| Fail-closed / fail-open | Kapalı / açık hata davranışı | Block vs allow when evaluation cannot complete. |
| Step-up approval | Step-up / ek onay | User must confirm or justify before continuing. |
| Session history | Oturum geçmişi | Recent tagged actions of the same session/agent/user, read by L2. |
| UMAI Cloud (SaaS) | UMAI Bulut (SaaS) | Multi-tenant deployment operated by UMAI, one stack per region (ADR-0018). |
| Self-hosted | Kurum içi kurulum (on-prem) | The same release run by the customer on its own servers, possibly air-gapped. |
| Edition | Sürüm / paket | Community or Enterprise: a named set of entitlements, never a separate build. |
| Entitlement | Yetkilendirme / hak | A tenant's limits (monthly requests, max prompt size) and features (SSO, RBAC, SIEM, version pinning), loaded as signed data (ADR-0019). |
| Licence file | Lisans dosyası | Signed entitlement record for a self-hosted install, verified offline. |
| Tenant | Kiracı / müşteri | A customer organisation; in SaaS its identity comes from its API key. |
| Organization | Kuruluş | The sign-in face of a tenant: one identity-service organization per tenant, mirrored from our tenant store (ADR-0033). |
| Membership | Üyelik | A person's place in an organization, with roles; kept in our store, mirrored to the identity service. |
| Invitation | Davet | Single-use, expiring offer to join an organization, bound to an e-mail address. |
| Owner | Sahip | Role that may delete or hand over the organization; every organization keeps at least one. |
| Verified domain | Doğrulanmış alan adı | An e-mail domain an organization has proved by DNS, used for join requests or auto-join. |
| Hosted login | Barındırılan giriş sayfası | The identity service's own sign-in pages (Auth0 Universal Login, Keycloak login theme); the console never shows a password field. |
| Data residency | Veri yerleşimi | The region whose stack processes and stores a tenant's data (EU or US). |
| TCKN | T.C. Kimlik Numarası | 11-digit Turkish national ID with checksum. |
| VKN | Vergi Kimlik Numarası | 10-digit Turkish tax ID with checksum. |
| KVKK | Kişisel Verilerin Korunması Kanunu | Turkish personal data protection law (Law No. 6698). |
| ADR (architecture) | Mimari karar kaydı | A record in `docs/adr/`. Not to be confused with the UMAI ADR Collector product. |
| ADR Collector | ADR Collector | UMAI's passive desktop agent-log collector. Unrelated to architecture decision records. |
