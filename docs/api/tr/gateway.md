# AI Gateway

AI Gateway OpenAI'nin API'sini konuşur. Bir OpenAI SDK'sını UMAI anahtarıyla UMAI'ye
yönlendirin; her çağrı, gidişte ve dönüşte uygulamanızın guardrail'i tarafından denetlenir:
istemler, yanıtlar, araç çağrıları ve araç çıktıları. Guardrail'in maskelediği değerler modele
asla ulaşmaz; kendi mesajlarınızdan maskelenen değerler, yanıt size ulaşmadan önce yerine konur.

`gateway` kapsamlı bir anahtar gerektirir ([Kimlik doğrulama](authentication.md)).

| Uç nokta | OpenAI API | Denetimler |
|---|---|---|
| `POST /v1/chat/completions` | Chat Completions, akış dâhil | Girdi, araç çıktıları, yanıt, araç çağrıları |
| `POST /v1/responses` | Responses API, akış dâhil | Girdi, araç çıktıları, yanıt, araç çağrıları |
| `POST /v1/embeddings` | Embeddings | Girdi (her metin) |
| `GET /v1/models` | Models | Yok; aylık istek hakkınızdan asla düşmez |

## Bağlanmak

| Ayar | Değer |
|---|---|
| Temel adres (base URL) | `https://<umai-address>/v1` |
| API anahtarı | UMAI anahtarınız: `Authorization: Bearer <UMAI anahtarı>` |
| `X-Umai-Provider-Key` | Sağlayıcı anahtarınız (ör. OpenAI'ninki), sağlayıcı sizin anahtarınızla bağlandıysa. Yerleşik OpenAI sağlayıcısında durum budur. UMAI bunu yalnızca sağlayıcıya gönderir; asla saklamaz ya da kaydetmez |

Kuruluşunuz sağlayıcıları (OpenAI, Azure OpenAI, OpenAI uyumlu sunucular) konsolda bağlar.
UMAI'nin tuttuğu bir anahtarla kurulmuş bir sağlayıcı `X-Umai-Provider-Key` gerektirmez.

### Hangi uygulama, hangi model

- Anahtarın uygulaması kullanılır. Uygulamaya ait olmayan bir anahtar `X-Umai-Application`
  başlığını ya da kuruluşun tek uygulamasını kullanır.
- `model`, uygulama onu eşlemedikçe sağlayıcıya gönderdiğiniz gibi iletilir: bir uygulama kabul
  ettiği adları sınırlayabilir (**404** `model_not_allowed`) ve takma adlar tanımlayabilir.
- `@<provider>/<model>` çağrıyı kuruluşunuzun bağladığı başka bir sağlayıcıya gönderir, ör.
  `@azure-eu/gpt-4o-deployment`.
- `GET /v1/models` uygulamanın sunduğu adları listeler.

## Neler denetlenir

1. **Girdi.** Ağ geçidinin ilettiği her mesaj, geçmiş dâhil (önceki bir turda maskelenen değer
   geçmişte yeniden yer alır ve yeniden maskelenir) ve önceki araç çağrılarının argümanları.
   Guardrail `TOOL_OUTPUT` aşamasını etkinleştirdiyse her araç çıktısı aracın adıyla ayrı ayrı
   denetlenir.
2. **Yanıt.** Model yanıt verdikten sonra her seçeneğin (choice) metni.
3. **Araç çağrıları.** Guardrail `TOOL_INPUT` aşamasını etkinleştirdiyse yanıttaki her araç
   çağrısı, aracın adıyla. Maskelenmiş argümanlar geçerli JSON olarak kalır.

Çağrı yalnızca her girdi denetimi izin verirse sağlayıcıya gider. Metin parçaları denetlenir;
görseller, ses ve dosyalar denetlenemez ve uygulamanız bunları denetlenmeden iletecek şekilde
ayarlanmadıkça **400** `unsupported_content` ile reddedilir (bu durumda
`X-Umai-Unscanned-Parts` yanıt başlığı bunları sayar).

Bazı istek alanları içeriğin denetimi atlamasına izin verirdi, bu yüzden ağ geçidi onları ele
alır: `prediction`, ses çıktısı ve Responses `prompt.variables` reddedilir (**400**
`unsupported_content`); `metadata`, mesajların `name` alanı, `logprobs` / `top_logprobs` ve
akıl yürütme özetleri iletilmez. Diğer her alan değiştirilmeden iletilir.

### Maskeleme ve geri koyma

`ALLOW_WITH_MODIFICATIONS` durumunda ağ geçidi, çağrıdan önce her değeri `<TR_TCKN_1>` gibi bir
yer tutucuyla değiştirir. Modelin yanıtı sizin mesajlarınızdan gelen bir yer tutucu içerirse ağ
geçidi değeri yerine koyar; böylece kullanıcınız yazdığını görür. Modelin kendi ürettiği değerler
maskeli kalır. Değerleri asla geri göstermemesi gereken bir uygulama, geri koymayacak şekilde
ayarlanabilir.

## Reddetmeler

Varsayılan olarak bir reddetme, OpenAI'nin hata biçiminde **HTTP 400**'dür; OpenAI SDK'ları
`BadRequestError` fırlatır. `type` değeri `umai_guardrail`, `code` değeri `umai_blocked`'tır
(bir API'nin isteyemeyeceği bir onay kararı için `umai_step_up_required`); `umai` nedenini
türler ve kimliklerle söyler, değerleri asla içermez:

```json
{
  "error": {
    "message": "Blocked by UMAI guardrail gr-demo-data-protection: L0: TR_TCKN detected by policy Kimlik verileri v2",
    "type": "umai_guardrail",
    "code": "umai_blocked",
    "umai": {
      "request_id": "gw-3f9c1e0b",
      "event_id": "ta-195c97496f4748b88442e60f6ed6e1b9",
      "phase": "PRE_LLM",
      "action": "BLOCK",
      "entities": ["TR_TCKN"],
      "matched": ["identity-data"]
    }
  }
}
```

`umai.phase` hangi denetimin reddettiğini söyler: `PRE_LLM` (girdiniz; sağlayıcıya hiçbir şey
ulaşmadı), `TOOL_OUTPUT` (bir araç çıktısı), `POST_LLM` (yanıt) ya da `TOOL_INPUT` (yanıttaki bir
araç çağrısı).

Bir uygulama, hata gösteremeyen sohbet arayüzleri için reddetmeleri olağan bir yanıt olarak da
verebilir: HTTP 200, mesaj olarak ayarlanan metin ve `finish_reason: "content_filter"`
(Responses API'de `status: "incomplete"` ve `incomplete_details.reason: "content_filter"`).

## Akış

`"stream": true` OpenAI'deki gibi çalışır. Uygulamanızın akış modu, yanıtların akarken nasıl
denetleneceğini belirler:

| Mod | Ne görürsünüz |
|---|---|
| `window` (varsayılan) | Metin modelin biraz gerisinden gelir (kısa bir pencere tutulur ve denetlenir). Engelleme, seçeneği `finish_reason: "content_filter"` ile bitirir |
| `buffer` | İlk olaydan önce yanıtın tamamı denetlenir; engelleme yine HTTP 400 olabilir |
| `after` | Akış bekletilmez; yanıt sonunda denetlenir ve karar kaydedilir, uygulanmaz |

Akıştaki araç çağrıları denetlendikten sonra, tam olarak gelir. Sağlayıcı akışın ortasında yanıt
vermeyi bırakırsa `data: [DONE]` öncesinde `code: upstream_timeout` içeren bir hata olayı gelir.

## Yanıt başlıkları

| Başlık | Anlamı |
|---|---|
| `X-Umai-Request-Id` | Çağrının kimliği (aynı adlı istek başlığından sizinki ya da üretilen); bize ulaşırken belirtin |
| `X-Umai-Application` | Çağrının çalıştığı uygulama |
| `X-Umai-Event-Id` | Girdi denetiminin kaydedilen kararı |
| `X-Umai-Input-Decision` | Girdi denetiminin eylemi |
| `X-Umai-Output-Decision` | Yanıt denetimlerinin en katı eylemi (akışsız ve `buffer`) |
| `X-Umai-Unscanned-Parts` | Denetlenmeden iletilen parçalar |
| `X-Umai-Upstream-Error` | `true`: gövde sağlayıcının kendi hatasıdır, değiştirilmeden iletilmiştir |
| `X-Umai-Fallback` | Uygulamanın yedek sağlayıcısı yanıt verdi |

İsteğe bağlı istek başlıkları, onları kullanan politikalar ve kayıtlarınız için kimin işlem
yaptığını anlatır: `X-Umai-Session-Id`, `X-Umai-Subject-User` (takma kimlik; yoksa gövdedeki
`safety_identifier` ya da `user`), `X-Umai-Subject-Role`, `X-Umai-Subject-Department`,
`X-Umai-Agent-Id`.

## Örnekler

### curl

```bash
curl https://<umai-address>/v1/chat/completions \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "X-Umai-Provider-Key: $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder."}]}'
```

### Python

```python
import os

import openai
from openai import OpenAI

client = OpenAI(
    base_url="https://<umai-address>/v1",
    api_key=os.environ["UMAI_API_KEY"],
    default_headers={"X-Umai-Provider-Key": os.environ["OPENAI_API_KEY"]},
)

try:
    answer = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "user", "content": "10000000146 numaralı müşterinin kredi limitini özetle."}
        ],
        extra_headers={"X-Umai-Session-Id": "s-0192"},
    )
    print(answer.choices[0].message.content)
except openai.BadRequestError as error:
    if error.code in ("umai_blocked", "umai_step_up_required"):
        umai = error.body["umai"]  # why, with types and ids only
        print(f"refused at {umai['phase']}: {umai['entities']} (event {umai['event_id']})")
    else:
        raise

# Streaming
stream = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[
        {"role": "user", "content": "Müşteri hizmetleri için kısa bir karşılama mesajı yaz."}
    ],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
    if chunk.choices and chunk.choices[0].finish_reason == "content_filter":
        print("\n[stopped by your organization's AI policy]")

# Responses API and embeddings use the same client
response = client.responses.create(model="gpt-4.1-mini", input="Kısa bir teşekkür notu yaz.")
vectors = client.embeddings.create(
    model="text-embedding-3-small", input=["İade talebi için başvuru numaranızı kullanın."]
)
```

### JavaScript

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://<umai-address>/v1",
  apiKey: process.env.UMAI_API_KEY,
  defaultHeaders: { "X-Umai-Provider-Key": process.env.OPENAI_API_KEY },
});

try {
  const answer = await client.chat.completions.create({
    model: "gpt-4.1-mini",
    messages: [{ role: "user", content: "10000000146 numaralı müşterinin kredi limitini özetle." }],
  });
  console.log(answer.choices[0].message.content);
} catch (error) {
  if (error instanceof OpenAI.APIError && error.code === "umai_blocked") {
    const umai = error.error.umai; // why, with types and ids only
    console.log(`refused at ${umai.phase}: ${umai.entities} (event ${umai.event_id})`);
  } else {
    throw error;
  }
}
```

### OpenAI SDK'larında yeniden denemeler

OpenAI SDK'ları 429 ve 5xx yanıtlarını varsayılan olarak iki kez yeniden dener. Bir guardrail
reddi (400) asla yeniden denenmez. `quota_exceeded` gelecek aydan önce kalkmaz: `error.code`
değerini kontrol edin ve yeniden denemeyi bırakın (bkz. [Sınırlar](limits.md)).
