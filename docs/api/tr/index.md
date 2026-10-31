# UMAI API belgeleri

UMAI, uygulamalarınızın yapay zekâ modellerine gönderdiklerini ve modellerden gelenleri
denetler: kişisel veriler (T.C. kimlik numaraları, IBAN'lar, kart numaraları, e-posta adresleri
ve daha fazlası), gizli bilgiler, istem saldırıları ve kötüye kullanım, kuruluşunuzun UMAI
konsolunda kurduğu **guardrail**'e göre değerlendirilir. Her denetim bir kararla biter: içerik
olduğu gibi geçer, hassas değerler maskelenerek geçer ya da durdurulur.

Bir uygulamayı bağlamanın iki yolu vardır.

| | AI Gateway | Değerlendirme API |
|---|---|---|
| Neyi değiştirirsiniz | OpenAI SDK'nızın temel adresini ve API anahtarını | Model çağrınızdan önce ve sonra UMAI'yi çağırırsınız |
| Denetimler | İstemler, yanıtlar, araç çağrıları ve araç çıktıları, kendiliğinden | Gönderdiğiniz içerik, seçtiğiniz adımda |
| Maskelenen değerler | Yanıta sizin için geri konur | Maskelenmiş metni alırsınız |
| Modeller | Kuruluşunuzun bağladığı OpenAI, Azure OpenAI ve OpenAI uyumlu sağlayıcılar | Herhangi bir model ya da sağlayıcı, ya da hiçbiri |
| Uç noktalar | `POST /v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `GET /v1/models` | `POST /v1/evaluate` |
| Rehber | [AI Gateway](gateway.md) | [Değerlendirme API](evaluate.md) |

## Başlamadan önce

1. Konsolda **Uygulamalar**'ı açın, bir uygulama oluşturun ve guardrail'ini seçin
   (kuruluşunuz hemen kullanabileceğiniz bir başlangıç guardrail'iyle başlar).
2. Uygulama için bir anahtar oluşturun. Anahtar yalnızca bir kez gösterilir; gizli bilgi
   deponuzda saklayın. Bkz. [Kimlik doğrulama](authentication.md).
3. **UMAI adresinizi** konsoldaki **API ve SDK'lar** sayfasında bulun. Aşağıdaki örneklerde
   `https://<umai-address>` olarak yazılıdır.

UMAI'yi yalnızca sunucularınızdan çağırın. Tarayıcıdaki ya da mobil uygulamadaki bir anahtarı
uygulamayı kullanan herkes okuyabilir.

## AI Gateway üzerinden ilk çağrı

Ağ geçidi OpenAI'nin API'sini konuşur. Resmî SDK'yı UMAI'ye yönlendirin ve (kuruluşunuz yerleşik
OpenAI sağlayıcısını kullanıyorsa) OpenAI anahtarınızı `X-Umai-Provider-Key` başlığında gönderin:

```bash
curl https://<umai-address>/v1/chat/completions \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "X-Umai-Provider-Key: $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Write a short welcome message for new customers."}]}'
```

```python
import os

from openai import OpenAI

client = OpenAI(
    base_url="https://<umai-address>/v1",
    api_key=os.environ["UMAI_API_KEY"],
    default_headers={"X-Umai-Provider-Key": os.environ["OPENAI_API_KEY"]},
)
answer = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[{"role": "user", "content": "Write a short welcome message for new customers."}],
)
print(answer.choices[0].message.content)
```

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://<umai-address>/v1",
  apiKey: process.env.UMAI_API_KEY,
  defaultHeaders: { "X-Umai-Provider-Key": process.env.OPENAI_API_KEY },
});
const answer = await client.chat.completions.create({
  model: "gpt-4.1-mini",
  messages: [{ role: "user", content: "Write a short welcome message for new customers." }],
});
console.log(answer.choices[0].message.content);
```

## Değerlendirme API'sine ilk çağrı

```bash
curl https://<umai-address>/v1/evaluate \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contract_version": "0.12", "request_id": "req-0001", "timestamp": "2026-10-31T09:30:00Z", "phase": "PRE_LLM", "content": {"messages": [{"role": "user", "content": "10000000146 numaralı müşterinin kredi limitini özetle."}], "focus": "LAST_USER_MESSAGE"}}'
```

Test kimlik numarası `10000000146` başlangıç guardrail'i tarafından engellenir; yanıt bunu
`decision.action` alanında söyler ve numarayı hiçbir zaman tekrarlamaz. Aynı çağrının Python ve
JavaScript hâli [Değerlendirme API](evaluate.md) rehberindedir.

## Sürümler

Her API'nin bir sözleşme sürümü vardır ve her sayfanın altında gösterilir. Küçük (minor) bir
sürüm mevcut çağıranları bozmadan bir şey ekler (bir alan, bir hata kodu); sizin tarafınızda iş
gerektiren bir değişiklikten önce tasarım ortaklarımıza haber veririz. Değerlendirme API'sinde
ayrıca kullandığınız istek biçiminin sürümünü, `contract_version` alanını (şu an `"0.12"`)
gönderirsiniz: yalnızca istek değiştiğinde değişir, bu yüzden API'nin sürümünün gerisinde
olabilir.

Makinece okunabilir tanımlar başvuru sayfalarındadır:
[Değerlendirme API](reference-evaluate.md) ve [AI Gateway](reference-gateway.md).

## Verileriniz

- UMAI'nin yanıtları, hata mesajları ve kayıtları varlık türlerini (`TR_TCKN`, `EMAIL_ADDRESS`)
  ve kimlikleri adlandırır, bulduğu değerleri asla içermez.
- `subject.user_id`, `X-Umai-Subject-User` ve `request_id` alanlarında e-posta adresi ya da ad
  değil, takma (pseudonim) kimlikler kullanın.
- Entegrasyon sırasında bu sayfalardaki gibi sentetik test verileri kullanın.

## Yardım almak

Bize ulaşırken bir değerlendirmenin `event_id` değerini ya da bir ağ geçidi çağrısının
`X-Umai-Request-Id` yanıt başlığını belirtin: kararı içeriğinizi görmeden bulabiliriz.
