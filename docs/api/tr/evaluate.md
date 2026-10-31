# Değerlendirme API

`POST /v1/evaluate` tek bir içeriği denetler ve uygulamanız gereken kararı döndürür. Sunucunuzdan,
bir istemi modele göndermeden önce, model yanıt verdikten sonra ve bir ajanın araç çağrılarının
çevresinde çağırın. Her modelle ve her sağlayıcıyla çalışır.

`evaluate` kapsamlı bir anahtar gerektirir ([Kimlik doğrulama](authentication.md)).

## Ne zaman çağrılır

| `phase` (aşama) | Denetlediğiniz | `content.focus` |
|---|---|---|
| `PRE_LLM` | Kullanıcı istemi, modele göndermeden önce | `LAST_USER_MESSAGE` |
| `POST_LLM` | Model yanıtı, göstermeden ya da kullanmadan önce | `LAST_ASSISTANT_MESSAGE` |
| `TOOL_INPUT` | Modelin yapmak istediği araç çağrısının argümanları; `action.name` ve `action.tool_name` alanlarını doldurun | `LAST_USER_MESSAGE` |
| `TOOL_OUTPUT` | Bir aracın döndürdüğü çıktı, modele geri gitmeden önce | `LAST_USER_MESSAGE` |
| `MCP_REQUEST`, `MCP_RESPONSE`, `MEMORY_WRITE` | MCP çağrıları ve ajan belleğine yazma; [başvurudaki](reference-evaluate.md) `action` alanına bakın | `LAST_USER_MESSAGE` |

Guardrail'inizin etkinleştirdiği aşamaları kullanın (konsol bunları gösterir). Etkinleştirmediği
bir aşama, guardrail'in hata politikasıyla ve `errors` içinde `CONFIG_INVALID` ile yanıtlanır.

## İstek

```json
{
  "contract_version": "0.12",
  "request_id": "req-7d2c41",
  "timestamp": "2026-10-31T09:30:00Z",
  "phase": "PRE_LLM",
  "content": {
    "messages": [
      { "role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder." }
    ],
    "focus": "LAST_USER_MESSAGE",
    "language": "tr"
  },
  "subject": { "user_id": "u-5b81", "session_id": "s-0192" },
  "destination": { "app": "support-assistant", "class": "external_llm" }
}
```

| Alan | Zorunlu | Ne gönderilir |
|---|---|---|
| `contract_version` | evet | `"0.12"`, istek biçiminin sürümü |
| `request_id` | evet | Çağrı için sizin kimliğiniz (en fazla 128 karakter); yanıtta geri döner |
| `timestamp` | evet | Çağrıyı yaptığınız zaman, RFC 3339 |
| `phase` | evet | Bkz. [Ne zaman çağrılır](#ne-zaman-cagrilir) |
| `content.messages` | evet | Konuşma, en eskiden başlayarak: `{role, content}`, rol `system`, `user` ya da `assistant`. Hangi kısmın taranacağına guardrail karar verir; varsayılan son mesajdır |
| `content.focus` | evet | `LAST_USER_MESSAGE` ya da `LAST_ASSISTANT_MESSAGE` |
| `content.language` | hayır | `tr`, `en`, …; gönderilmezse tespit edilir |
| `content.artifacts` | hayır | Denetime ait araç çağrıları, araç çıktıları ya da dosyalar |
| `subject` | hayır | Kim işlem yapıyor: `user_id`, `role`, `department`, `agent_id`, `session_id`. Takma kimlikler kullanın |
| `destination` | hayır | İçeriğin gittiği yer: `app` ve `class` (`external_llm`, `internal_llm`, `internal_app`, `external_app`, `agent_tool`, `unknown`) |
| `action` | ajan aşamalarında | `name`, `tool_name` (`TOOL_INPUT`), `server_name` ve `method` (`MCP_REQUEST`), `memory_scope` (`MEMORY_WRITE`) |
| `bundle` | hayır | Bir uygulamanın anahtarıyla göndermeyin. Uygulamaya ait olmayan bir anahtar guardrail'i belirtir: `{"id": "gr-demo-data-protection"}` |
| `timeout_ms` | hayır | Değerlendirme için süre bütçesi, 50–60 000 ms; hizmet tarafından sınırlanır |
| `flags.allow_model_calls` | hayır | `false`, karar modeli politikalarını atlar (daha hızlı); böyle bir politika uygulanacak idiyse yanıt `degraded` olarak işaretlenir |
| `tenant_id` | hayır | Göndermeyin; isteğin kime ait olduğunu anahtar söyler |

Bilinmeyen alanlar reddedilir (**422** `invalid_request`), böylece bir yazım hatası gözden
kaçmaz. Tüm mesajların ve eklerin (artifacts) karakterleri toplamı sınırınızı aşamaz (varsayılan
32 000, **413** `input_too_large`; bkz. [Sınırlar](limits.md)).

## Yanıt

```json
{
  "contract_version": "0.12",
  "request_id": "req-7d2c41",
  "tenant_id": "org-3f9a",
  "bundle": { "id": "gr-demo-data-protection", "version": 4, "mode": "ENFORCE", "sha256": "70f8e248132ea25c5a7496e6c2e5d3bbad7d354d088fa9478e6a228795b161a2" },
  "phase": "PRE_LLM",
  "decision": {
    "action": "ALLOW_WITH_MODIFICATIONS",
    "allowed": true,
    "severity": "MEDIUM",
    "reason": "L0: EMAIL_ADDRESS detected by policy İletişim bilgileri v1",
    "decided_by": "L0",
    "matched": [
      { "kind": "l0_policy", "id": "contact-data", "version": 1, "name": "İletişim bilgileri", "action": "ALLOW_WITH_MODIFICATIONS", "entities": ["EMAIL_ADDRESS"] }
    ]
  },
  "degraded": false,
  "tags": [ { "code": "data.pii.email", "layer": "L0", "score": 0.9 } ],
  "entities": [ { "type": "EMAIL_ADDRESS", "count": 1, "distinct": 1 } ],
  "output_modifications": {
    "redacted_text": "Bu e-postayı <EMAIL_ADDRESS_1> adresine gönder.",
    "entities": ["EMAIL_ADDRESS"]
  },
  "event_id": "ta-8714df5c9fb046fd8d88e5e5ee2830ff",
  "review_queued": false,
  "latency_ms": { "total": 2.3, "l0": 2.2 },
  "errors": []
}
```

Kimlik doğrulamasını ve doğrulamayı geçen her istek, değerlendirmenin bir kısmı başarısız olsa
bile, kararla birlikte HTTP 200 alır. Yanıt varlık türlerini ve politika kimliklerini adlandırır,
bulduğu değerleri asla içermez.

## Karara göre davranmak

| `decision.action` | `allowed` | Ne yapmalı |
|---|---|---|
| `ALLOW` | `true` | Devam edin |
| `ALLOW_WITH_WARNINGS` | `true` | Devam edin; uyarıyı kaydedebilir ya da gösterebilirsiniz |
| `ALLOW_WITH_MODIFICATIONS` | `true` | Taranan mesaj yerine `output_modifications.redacted_text` ile devam edin |
| `STEP_UP_APPROVAL` | `false` | Uygulamanızda bir kişi onaylamadıkça durun; onay adımınız yoksa `BLOCK` gibi davranın |
| `BLOCK` | `false` | Durun. Kendi mesajınızı gösterin; `decision.reason` son kullanıcılar için değil, kayıtlar içindir |

`decision.matched` kararı üreten politikaları en katıdan başlayarak listeler; `bundle` tam
guardrail sürümünü adlandırır. Böylece her karar, `event_id` değeriyle konsolun kayıtlarında
izlenebilir.

### Maskelenmiş metin

`ALLOW_WITH_MODIFICATIONS` durumunda `redacted_text`, UMAI'nin taradığı metindir (varsayılan
olarak son mesaj); listelenen türlerin her değeri `<EMAIL_ADDRESS_1>` ya da `<TR_TCKN_1>` gibi
bir yer tutucuyla değiştirilmiştir. Aynı değer, aynı yanıt içinde aynı yer tutucuyu alır.
Göndermeden önce taranan mesajı bununla değiştirin. Değerlendirme API'si değerleri geri vermez;
kullanıcınızın bunları modelin yanıtında görmesi gerekiyorsa kendi eşlemenizi tutun ya da
değerleri sizin için geri koyan [AI Gateway](gateway.md)'i kullanın.

### Yalnızca izleme modu

`MONITOR` modundaki bir guardrail kararları uygulamadan kaydeder: `decision.action` `ALLOW`
olur, `would_have` ise `ENFORCE` modunun döndüreceği kararı içerir. Bir guardrail'i uygulamaya
almadan önce gerçek trafikte denemek için kullanın.

### UMAI içinde bir şey başarısız olduğunda

Değerlendirmenin bir kısmı başarısız olursa (bir karar modeli zaman aşımına uğrar, guardrail
bulunamaz) yanıt yine HTTP 200'dür: `degraded` `true` olur, `errors` neyin başarısız olduğunu
söyler ve karar zaten guardrail'inizin hata politikasına uyar (örneğin kapalı kalacak şekilde
ayarlıysa `BLOCK`). Her zamanki gibi `decision` alanına göre davranın. Türler
[hata kataloğunda](errors.md#karar-icindeki-hatalar) listelenir.

UMAI hiç yanıt veremediğinde (ağ hatası, zaman aşımı, HTTP 429, 5xx) ne olacağına kendi
kodunuzda karar verin: hassas veriler için kapalı kalın (istemi göndermeyin), erişilebilirliğin
daha önemli olduğu yerde açık kalın. HTTP istemcinizin zaman aşımını `timeout_ms` değerinin biraz
üstüne ayarlayın.

## Örnekler

Bir istemi denetleyin, (gerekirse maskelenmiş olarak) gönderin, sonra yanıtı denetleyin.

### curl

```bash
curl https://<umai-address>/v1/evaluate \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contract_version": "0.12", "request_id": "req-7d2c41", "timestamp": "2026-10-31T09:30:00Z", "phase": "PRE_LLM", "content": {"messages": [{"role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder."}], "focus": "LAST_USER_MESSAGE"}}'
```

### Python

```python
import os
import uuid
from datetime import UTC, datetime

import httpx

UMAI = httpx.Client(
    base_url="https://<umai-address>",
    headers={"Authorization": f"Bearer {os.environ['UMAI_API_KEY']}"},
    timeout=5.0,
)


def check(phase: str, messages: list[dict], focus: str) -> dict:
    response = UMAI.post(
        "/v1/evaluate",
        json={
            "contract_version": "0.12",
            "request_id": f"req-{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(UTC).isoformat(),
            "phase": phase,
            "content": {"messages": messages, "focus": focus},
        },
    )
    response.raise_for_status()  # 4xx/5xx: your own failure behaviour applies
    return response.json()


messages = [{"role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder."}]
result = check("PRE_LLM", messages, "LAST_USER_MESSAGE")
decision = result["decision"]
if not decision["allowed"]:
    raise PermissionError(f"blocked by UMAI ({result['event_id']})")
if decision["action"] == "ALLOW_WITH_MODIFICATIONS":
    messages[-1]["content"] = result["output_modifications"]["redacted_text"]

answer = call_your_model(messages)  # your model call
messages.append({"role": "assistant", "content": answer})
result = check("POST_LLM", messages, "LAST_ASSISTANT_MESSAGE")
if not result["decision"]["allowed"]:
    answer = "Sorry, I can't share that."
elif result["decision"]["action"] == "ALLOW_WITH_MODIFICATIONS":
    answer = result["output_modifications"]["redacted_text"]
```

### JavaScript

```javascript
import { randomUUID } from "node:crypto";

async function check(phase, messages, focus) {
  const response = await fetch("https://<umai-address>/v1/evaluate", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.UMAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      contract_version: "0.12",
      request_id: `req-${randomUUID()}`,
      timestamp: new Date().toISOString(),
      phase,
      content: { messages, focus },
    }),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) {
    // 4xx/5xx: your own failure behaviour applies
    const body = await response.json().catch(() => ({}));
    throw new Error(`UMAI ${response.status} ${body.detail?.error ?? ""}`);
  }
  return response.json();
}

const messages = [{ role: "user", content: "Bu e-postayı ayse.yilmaz@example.com adresine gönder." }];
const result = await check("PRE_LLM", messages, "LAST_USER_MESSAGE");
if (!result.decision.allowed) throw new Error(`blocked by UMAI (${result.event_id})`);
if (result.decision.action === "ALLOW_WITH_MODIFICATIONS") {
  messages.at(-1).content = result.output_modifications.redacted_text;
}
```

### Bir araç çağrısı

Ajanınız bir aracı çalıştırmadan önce, modelin seçtiği argümanları denetleyin:

```json
{
  "contract_version": "0.12",
  "request_id": "req-7d2c42",
  "timestamp": "2026-10-31T09:30:02Z",
  "phase": "TOOL_INPUT",
  "content": {
    "messages": [
      { "role": "assistant", "content": "{\"customer_id\": \"10000000146\", \"fields\": [\"credit_limit\"]}" }
    ],
    "focus": "LAST_USER_MESSAGE",
    "content_type": "json"
  },
  "action": { "name": "lookup_customer", "tool_name": "lookup_customer" }
}
```

Guardrail `TOOL_INPUT` aşamasını etkinleştirmelidir; politikalar ve istisnalar o zaman aracın
adına başvurabilir.
