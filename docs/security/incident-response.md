# Incident response runbook (Stage 0)

**Status:** 2026-10-31, draft for the Stage 0 private beta (launch-plan S0-12). It covers `umai-prod`
(design partners, ADR-0050) and `umai-beta` (ADR-0032), the alerts of ADR-0051 and the status page.
Rehearse it once as a tabletop exercise before the first partner signs in, and record the exercise
in §9. Owners are roles (launch plan §1): **ops** runs it, **company** owns the legal notices.

Operational detail: [`deploy/azure/README.md`](../../deploy/azure/README.md) (environments, alerts,
status page), [`deploy/vm/README.md`](../../deploy/vm/README.md) (the VM, backup, restore). Threats:
[threat model](threat-model.md).

## 1. What counts as an incident

Any of these is an incident; open one and decide the severity later if unsure:

- an alert of severity 0–2 from Azure Monitor or the external checks (ADR-0051);
- a design partner reporting errors, wrong decisions, slowness or something they should not see;
- a suspected security event: leaked credential or key, unknown SSH login, unexpected
  configuration change in the audit log, data of one organization visible to another, raw
  content in a log, record or export (ADR-0005);
- a failed backup two nights in a row, or a backup that cannot be restored.

## 2. Severity levels

| Severity | Meaning (any one) | Examples | Acknowledge | Status page and partners |
|---|---|---|---|---|
| **SEV1** | Prod unavailable or unusable for all partners; data exposure confirmed or likely; decisions systematically wrong (fail-open, guardrail not applied) | `/readyz` failing from every location; VM down; one tenant's records readable by another; an API key or the signing key leaked; content sent somewhere it should not go | 15 min (on-call hours), 1 h otherwise | Status page within 30 min; direct notice to every affected partner within 1 h |
| **SEV2** | Prod degraded, or one partner fully affected; data at risk but no exposure known | 5xx above threshold; p95 latency above budget; L1b timeouts blocking traffic (fail-closed); disk nearly full; a partner's key or quota misbehaving; prod backup missing for 24 h | 1 h (on-call hours) | Status page if partners notice it; direct notice to the affected partner within 4 h |
| **SEV3** | Minor or internal: no partner impact yet | Beta down; one container restarting; certificate expiring in < 14 days; quota 429s for a partner (by design, but call them) | Next business day | None, or a note to the partner |

Raise the severity as soon as the facts justify it; lower it only when the IC says so. A security
event is at least SEV2 until ruled out.

**On-call hours in Stage 0:** Monday–Friday 08:00–20:00 Europe/Istanbul, best effort outside them,
as the beta agreement states (no SLA). The on-call person has the alert e-mail on their phone with
notifications on, SSH access to prod (key and current NSG address) and access to the status page.
Rotation: weekly, handed over on Monday morning with open alerts and changes; the schedule lives
next to the team calendar (S0-11 evidence).

## 3. Roles

One person may hold several roles in a small team, but the IC never also runs commands on the VM
during SEV1.

| Role | Does | Default |
|---|---|---|
| **Incident commander (IC)** | Declares the incident and its severity, decides (rollback, offline, notify), keeps the timeline, ends the incident | The on-call person until handed over |
| **Operator** | Hands on Azure and the VM: diagnosis, rollback, restore; says what they run before running it | Another engineer; the IC if alone (then SEV1 waits for a second person for destructive steps) |
| **Communications** | Status page, partner e-mails (templates §6), support channel; nothing is sent without the IC's OK | Product owner or IC |
| **Security lead** | For security events: evidence, scope of exposure, key rotation, breach assessment (§7) | Engineering lead |
| **Company / counsel** | Legal notices to controllers and authorities (§7), contract questions | Company (management) |

## 4. The first hour

Write every step with a UTC timestamp in the incident log (a shared document named
`INC-YYYYMMDD-<n>`, never containing secrets or customer content).

**0–5 min: declare**

1. Acknowledge the alert (Azure portal → Monitor → Alerts → *Change state* → Acknowledged) so the
   others know someone has it.
2. Open the incident log, name the IC, set a first severity.
3. Look at the external check / status page: is it down from outside, or only from Azure?

**5–15 min: assess**

4. Azure: Monitor → Alerts (all firing for this environment); VM state in the portal; Azure
   status page for West Europe.
5. On the VM (prod key): `sudo /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh status`;
   `sudo docker compose -p umai logs --since 30m --tail 200 engine` (also `policy`, `gateway`,
   `console`, `caddy`, `postgres`). Logs carry no content (ADR-0005).
6. Last change: what was deployed or configured, and when (`git tag -l 'prod-*'`, the audit log
   in the console, `/opt/umai/config.env` modification time).
7. Decide: customer impact? security relevance? If security: call the security lead now and
   follow §4a in parallel.

**15–30 min: stabilise and say so**

8. SEV1/SEV2 with partner impact: Communications posts "Investigating" on the status page
   (template §6.1).
9. Pick the smallest action that stops the harm, in this order of preference: roll back the last
   change (§5.1); restart the failing container (`docker compose -p umai restart <service>`); turn
   off the failing optional part (gateway `UMAI_GATEWAY=false`, or the L1b key, which makes guardrails
   use their failure policy); take the stack offline (§4b). Say the command in the channel before
   running it.

**30–60 min: inform and plan**

10. Direct notice to affected partners (§6.2), even if the cause is unknown.
11. Next status update time fixed (every 30 min for SEV1, every 2 h for SEV2) and kept.
12. Security events: decide by minute 60 whether personal data may be involved; if yes the
    breach clock (§7) has started and company/counsel are told now.

**After the hour:** keep updating until resolved; post "Resolved" (§6.3); write the post-incident
review within 5 business days (§8).

### 4a. Security events: preserve before you fix

- Do not delete the VM, containers, volumes or logs. Before changing anything, if the VM may be
  compromised: snapshot the OS disk (portal → disk → Create snapshot; needs the user's OK as any
  Azure change) and copy the last 24 h of container logs off the box
  (`docker compose -p umai logs --since 24h > /var/tmp/inc-<n>.log`, then `scp`).
- Log Analytics keeps 30 days of logs (ADR-0051): export the relevant query results into the
  incident log as files.
- Rotate what may have leaked, in this order: the affected API keys (§4b), operator tokens and the
  session secret (`umai.sh set-secret`, `umai.sh up`; everyone signs in again), the OpenAI project
  key, the backup SAS. The bundle signing key and the tenant HMAC master key change hashes and
  signatures: rotate them only with the security lead's decision (`deploy/vm/README.md` §4).

### 4b. Kill switches

| Scope | How | Undo |
|---|---|---|
| One API key | Console: Applications → Keys → Revoke; or as operator inside the policy container: `DELETE /v1/tenants/<tenant>/keys/<key id>` | Create a new key (secrets are shown once) |
| All keys of one tenant | List with `GET /v1/tenants/<tenant>/keys`, revoke each | New keys |
| The gateway | `UMAI_GATEWAY=false` in `/opt/umai/config.env`, `umai.sh up`, `docker compose -p umai stop gateway` | `true`, `umai.sh up` |
| The whole public endpoint | `sudo docker compose -p umai stop caddy` (data and services stay; every request fails) | `umai.sh up` |
| The whole stack | `sudo systemctl stop umai` | `sudo systemctl start umai` |

Operator calls to the policy service: `sudo docker exec -i umai-policy-1 python -` with
`UMAI_DE_CONTROL_API_TOKEN` and `X-Umai-Tenant` (`deploy/vm/README.md` §4, §7b). Exercise the
tenant kill switch on beta before S0 exit (launch-plan "Exit").

## 5. Rollback

Prod runs only commits that ran on beta and each prod release has a Git tag `prod-YYYYMMDD-<n>`
(ADR-0050 §4). Images are built on the workstation; the VM keeps no previous image (`umai.sh up`
prunes dangling images), so a rollback rebuilds the previous tag.

### 5.1 Code only (no migration in the release)

```bash
git fetch --tags
git worktree add --detach ../rb-engine prod-<previous>
git -C ../umai-control-center worktree add --detach ../rb-console <console commit of that release>
SSH_KEY=~/.ssh/umai_prod_ed25519 ../rb-engine/deploy/vm/ship.sh umaiops@<prod hostname> ../rb-console
```

Then the checks of `deploy/vm/README.md` §5. The release notes (PR list) say whether a release
contains a migration (`src/umai_decision_engine/db/migrations/versions/`).

### 5.2 Across a migration

The policy service upgrades the database when it starts; an older image does not know the newer
revision and will not start against it. Two ways back:

1. **Restore the backup taken right before the deploy** (preferred; required by ADR-0050 §5):
   `deploy/vm/README.md` §4 "Restore", with the previous release's images (§5.1). Everything
   written since that backup is lost: decision records, usage, audit events, keys and settings
   created in between. Say so in the partner notice; list the keys partners must recreate.
2. **Downgrade first** with the new image (`alembic downgrade <revision>` inside the policy
   container, owner connection), then ship the previous release. Keeps older data, drops what the
   new migration added. Only if the migration has a tested `downgrade()`.

### 5.3 Configuration

Guardrails: activate the previous version in the console or with the policy service's rollback
(ADR-0022); takes effect within one bundle poll. `config.env`: every edit starts with a dated copy
(`cp /opt/umai/config.env /opt/umai/config.env.bak-<date>-<what>`); restore it and `umai.sh up`.

### 5.4 Whole VM lost

Create it again from Bicep (`deploy/azure/README.md` §1, same parameters: the DNS label and so the
hostname come back), first-time setup (`deploy/vm/README.md` §3) **with the secrets from the
password manager copy of `/opt/umai/secrets.env`** (without them, bundle signatures, tenant HMAC
hashes and webhook secrets change), then restore the latest backup. Recovery point: the last
nightly backup (≤ 24 h). Recovery time: a few hours; measured by the restore drill.

## 6. Customer notice templates

Send from the support address to each partner's named contacts; post the public part on the status
page. No customer content, no other customer's name, no speculation about cause before it is known.
Times in UTC and Europe/Istanbul.

### 6.1 Status page: investigating / identified / resolved

**EN**
> **Investigating** — We are investigating [errors / slow responses / sign-in problems] on UMAI
> since HH:MM UTC. API calls may [fail / be slower]. Next update by HH:MM UTC.
>
> **Identified** — We found the cause: [one sentence, no internals]. We are [rolling back / fixing].
> Next update by HH:MM UTC.
>
> **Resolved** — The issue is resolved since HH:MM UTC. Between HH:MM and HH:MM UTC [what users saw].
> We will share a summary with affected customers.

**TR**
> **İnceleniyor** — UMAI'de HH:MM UTC'den (TSİ HH:MM) bu yana görülen [hataları / yavaş yanıtları /
> oturum açma sorunlarını] inceliyoruz. API çağrıları [başarısız olabilir / yavaşlayabilir].
> Bir sonraki güncelleme en geç HH:MM UTC'de.
>
> **Neden belirlendi** — Sorunun nedenini bulduk: [tek cümle, iç ayrıntı yok]. [Geri alma / düzeltme]
> yapıyoruz. Bir sonraki güncelleme en geç HH:MM UTC'de.
>
> **Çözüldü** — Sorun HH:MM UTC'den (TSİ HH:MM) bu yana çözüldü. HH:MM–HH:MM UTC arasında
> [kullanıcıların gördüğü etki]. Etkilenen müşterilerimizle bir özet paylaşacağız.

### 6.2 Direct notice to an affected partner (service incident)

**EN** — Subject: `[UMAI] Service incident on <date>: <short title>`

> Dear [name],
>
> Since HH:MM UTC, UMAI [has been unavailable / has returned errors for some requests / has been
> slower than usual] for [your organization / all customers]. [What this means for their
> integration, e.g. "requests to /v1/evaluate may fail; with a fail-closed integration your
> application will block them".]
>
> What we are doing: [rollback / fix / investigation]. What you may need to do: [nothing / retry
> failed requests / recreate API keys created after HH:MM UTC].
>
> We will update you by HH:MM UTC, and on [status page URL]. Contact: [support address].
>
> [Name], UMAI

**TR** — Konu: `[UMAI] <tarih> tarihli hizmet kesintisi: <kısa başlık>`

> Sayın [ad],
>
> HH:MM UTC'den (TSİ HH:MM) bu yana UMAI [kullanılamıyor / bazı isteklerde hata veriyor / her
> zamankinden yavaş yanıt veriyor]; bu durum [kuruluşunuzu / tüm müşterilerimizi] etkiliyor.
> [Entegrasyona etkisi, ör. "/v1/evaluate istekleri başarısız olabilir; hata durumunda engelleyen
> (fail-closed) bir entegrasyonda uygulamanız bu istekleri engelleyecektir."]
>
> Yaptığımız: [geri alma / düzeltme / inceleme]. Sizden beklenen: [bir işlem gerekmiyor /
> başarısız istekleri yeniden deneyin / HH:MM UTC'den sonra oluşturduğunuz API anahtarlarını
> yeniden oluşturun].
>
> En geç HH:MM UTC'de ve [durum sayfası adresi] üzerinden bilgi vereceğiz. İletişim: [destek adresi].
>
> [Ad], UMAI

### 6.3 Security incident notice to a partner (UMAI as processor)

Only after the IC, security lead and company agree on the facts; counsel reviews it when personal
data is involved (§7).

**EN** — Subject: `[UMAI] Security incident notification: <reference>`

> Dear [name],
>
> We are informing you, as required by our agreement, of a security incident affecting the UMAI
> service we provide to [organization].
>
> - **When:** detected on [date, HH:MM UTC]; [started on … / start not yet known].
> - **What happened:** [factual description].
> - **Data concerned:** [categories, e.g. console user names and e-mail addresses, API key
>   identifiers, decision records (which contain no request content), approximate number of
>   records / persons, or "under investigation"].
> - **Likely consequences:** [ … ].
> - **What we have done:** [containment, rotation, revocation].
> - **What we recommend you do:** [e.g. rotate the listed API keys; inform your users].
>
> We will send further information as soon as we have it, at the latest by [date]. Our contact
> for this incident: [name, address, phone].
>
> [Name], UMAI

**TR** — Konu: `[UMAI] Güvenlik ihlali bildirimi: <referans>`

> Sayın [ad],
>
> Sözleşmemiz gereği, [kuruluş] için sunduğumuz UMAI hizmetini etkileyen bir güvenlik olayını
> bildiririz.
>
> - **Ne zaman:** [tarih, HH:MM UTC] tarihinde tespit edildi; [… tarihinde başladı / başlangıcı henüz bilinmiyor].
> - **Ne oldu:** [olgusal açıklama].
> - **İlgili veriler:** [kategoriler, ör. konsol kullanıcılarının adları ve e-posta adresleri, API
>   anahtarı kimlikleri, karar kayıtları (istek içeriği içermez); yaklaşık kayıt / kişi sayısı ya
>   da "inceleniyor"].
> - **Olası sonuçlar:** [ … ].
> - **Aldığımız önlemler:** [sınırlama, anahtar değişimi, iptal].
> - **Önerdiğimiz adımlar:** [ör. listelenen API anahtarlarını yenileyin; kullanıcılarınızı bilgilendirin].
>
> Yeni bilgileri öğrendikçe ve en geç [tarih] tarihine kadar ileteceğiz. Bu olayla ilgili
> iletişim kişimiz: [ad, adres, telefon].
>
> [Ad], UMAI

## 7. Personal-data breaches: KVKK and GDPR — **verify with counsel**

> This section is an engineering summary to start the clock correctly. It is **not legal advice**
> and must be reviewed by counsel before the first partner signs in (S0-13). The beta agreement's
> processing terms override anything here.

**Roles.** For the content and records of a design partner's organization, UMAI is the
**processor** (GDPR Art. 4(8), Art. 28; KVKK Art. 3 "veri işleyen") and the partner is the
controller. For its own console accounts, support contacts and billing data UMAI is itself a
**controller**. Which role applies decides who notifies whom.

**What could be personal data here.** By design no request content is stored (ADR-0005,
ADR-0041) and raw content exists only in memory in L0 and the gateway (ADR-0035). Stored:
console users' names and e-mail addresses (Auth0 and the tenant store), user ids in audit events,
API key metadata and hashes, decision and usage records (ids, entity types, no content), client IP
addresses in Caddy and admission log lines and in Log Analytics (30 days), backups of all of it.
A breach of content in transit (gateway, L0) is still a breach even though nothing was stored.

**GDPR (EU partners, EU data subjects).**

- As processor: notify the controller **without undue delay** after becoming aware (Art. 33(2)),
  with what Art. 33(3) lists as far as known; information may follow in phases (Art. 33(4)).
- As controller: notify the competent supervisory authority **within 72 hours** of becoming aware
  unless the breach is unlikely to result in a risk; later notices state the reason for the delay
  (Art. 33(1)); notify data subjects without undue delay if high risk (Art. 34). Which authority is
  competent for UMAI (EU establishment, Art. 27 representative) is a question for counsel.
- Document every breach, notified or not (Art. 33(5)): the incident log and §8 review are that record.

**KVKK (Turkish partners, data subjects in Türkiye).**

- The controller notifies the data subject and the Board "in the shortest time" (Law 6698
  Art. 12(5)); Board decision 2019/10 (24.01.2019) reads this as **72 hours** from learning of the
  breach for the Board, and a reasonable time for data subjects once identified; the Board's
  breach-notification form is used, and a late notice gives the reason for the delay (*secondary
  sources*; confirm on kvkk.gov.tr).
- A processor's duty to inform the controller is not set out in Art. 12(5) itself: it comes from
  the processing agreement (*unverified*: counsel to confirm), and the beta agreement must give a
  deadline short enough for the partner to meet its 72 hours (engineering proposes 24 hours).
- Controller and processor are jointly responsible for security measures (Art. 12(2)).

**Practical rule for Stage 0.** When personal data may be involved, the IC records the time of
awareness; the security lead and company are told at once; the controller (partner) gets a first
notice (§6.3) within 24 hours even if incomplete; UMAI's own controller notifications are prepared
by counsel within the 72-hour window. Partner contacts for such notices are listed in each beta
agreement.

## 8. After the incident

Within 5 business days, a blameless review in `docs/security/incidents/INC-YYYYMMDD-<n>.md` (or the
company's document store if it names a customer): timeline in UTC, impact (partners, duration,
requests failed or blocked), root cause, what worked, what did not, actions with owners and
dates. Actions that change a decision get an ADR; alerts that fired too late or too often get
their thresholds changed in `deploy/azure/monitoring.bicepparam`. Share a summary with affected
partners.

## 9. Exercises

| Date | Kind | Scenario | Participants | Findings and actions |
|---|---|---|---|---|
| — | Tabletop (S0-12 evidence) | Proposed: prod `/readyz` failing after a release with a migration; then a partner reports seeing another organization's key names | ops, IC, communications, company | — |
| — | Alert drill (S0-11 evidence) | Stop the engine container on beta; check the alert e-mail arrives, the status page check turns red, acknowledge, restore | ops | — |
