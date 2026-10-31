# Sınırlar

| Sınır | Değerlendirme API | AI Gateway | Aşıldığında |
|---|---|---|---|
| Anahtar başına istek | Saniyede 20, anlık en fazla 40 | aynı | **429** `rate_limited`, `Retry-After` |
| Adres başına geçersiz anahtar | 60 saniyede 20 | aynı | **429** `too_many_failed_attempts`, `Retry-After` |
| Aylık istek | Planınızın sınırı, kuruluş başına; test anahtarlarının kendi sınırı vardır | aynı sayaç | **429** `quota_exceeded`, `Retry-After` |
| İçerik boyutu | Tüm mesajlar ve ekler üzerinden 32 000 karakter (planınız daha azını belirleyebilir) | Çağrı başına 256 000 karakter denetlenen metin | **413** `input_too_large` |
| Gövde boyutu | 512 KB | 4 MB | **413** (barındırma katmanından, JSON gövdesi olmayabilir) ya da `request_too_large` |
| `timeout_ms` | 50–60 000 ms | — | **422** `invalid_request` |
| `request_id` | 128 karakter | `X-Umai-Request-Id`: 128 karakter (uzunu kesilir) | **422** `invalid_request` |

Sayılar bugünkü UMAI barındırılan hizmetininkilerdir. Kendi sunucunuzdaki bir kurulum ya da
sözleşmeniz başka değerler belirleyebilir; hata gövdeleri her zaman uygulanan sınırı söyler.

## Hız sınırları

Her anahtarın, sürekli dolan bir saniye başına istek bütçesi ve bunun üstünde anlık bir payı
vardır. Bunun üstünde istek, `Retry-After` başlığıyla (saniye) **429** `rate_limited` ile
reddedilir; hiçbir şey denetlenmez ve istek aylık istek hakkınızdan düşmez. Yükü daha fazla
anahtara değil zamana yayın: hız sınırları hizmeti tüm müşteriler için korur.

## Çok fazla geçersiz anahtar

60 saniye içinde 20 geçersiz anahtar gönderen bir adres, hangi anahtarla olursa olsun,
`Retry-After` süresince reddedilir (**429** `too_many_failed_attempts`). Bkz.
[Kimlik doğrulama](authentication.md#cok-fazla-gecersiz-anahtar).

## Aylık istek hakkı

Planınız, kuruluşunuzun bir takvim ayında (UTC) yapabileceği istek sayısını belirler: kabul
edilen her `POST /v1/evaluate` çağrısı ve her AI Gateway çağrısı (Chat Completions, Responses,
embeddings) bir kez sayılır. `GET /v1/models` asla sayılmaz; reddedilen istekler (401, 403, 413,
422, 429) asla sayılmaz. Canlı ve test anahtarlarının ayrı sınırları vardır. Konsol planınızı ve
bu ayın kullanımını gösterir.

Ayın istek hakkı dolduğunda her istek, hiçbir şey denetlenmeden reddedilir:

```json
{
  "detail": {
    "error": "quota_exceeded",
    "detail": "this organization used its 10000 live requests for the month; the quota resets at 2026-11-01T00:00:00+00:00",
    "limit": 10000,
    "used": 10000,
    "environment": "live",
    "resets_at": "2026-11-01T00:00:00Z"
  }
}
```

Ağ geçidi OpenAI'nin hata biçiminde `code: quota_exceeded` ile yanıt verir; ikisi de gelecek
ayın ilk saniyesine kadar `Retry-After` taşır. Kendi hata davranışınız uygulanır: UMAI içeriği
denetlemeden geçirmez. Daha fazlasına ihtiyacınız varsa sınıra ulaşmadan önce bizimle konuşun.

## İçerik boyutu

Değerlendirme API'si her mesajın ve her ekin `content` ve `payload_summary` alanlarının
karakterlerini (Unicode kod noktaları) toplar: tarayabildiği her şeyi. Sınırınızın üstünde istek
reddedilir; hiçbir şey değerlendirilmez ve hiçbir şey kesilmez:

```json
{
  "detail": {
    "error": "input_too_large",
    "detail": "the content has 41250 characters; this deployment accepts at most 32000",
    "limit": 32000,
    "size": 41250,
    "unit": "characters"
  }
}
```

Yalnızca denetlenmesi gerekeni gönderin (guardrail varsayılan olarak son mesajı tarar; eski
geçmiş boyuta eklenir ama taranmaz) ve uzun belgeleri parçalar hâlinde denetleyin.

Ağ geçidi her çağrıda tüm konuşmayı denetler, bu yüzden sınırı daha büyüktür; daha önce taradığı
bir mesajı yeniden taramaz, böylece uzun bir konuşma da hızlı kalır.

## Yeniden denemek

| Yanıt | Yeniden denenir mi? |
|---|---|
| 429 `rate_limited`, `too_many_failed_attempts` | Evet, `Retry-After` saniye sonra |
| 429 `quota_exceeded` | Hayır, `resets_at` öncesinde değil |
| 502, 503, 504 | Evet, üstel geri çekilme (exponential backoff) ve rastgele gecikmeyle, birkaç kez |
| Diğer 4xx | Hayır: önce isteği ya da anahtarı düzeltin |
| `degraded: true` ile 200 | Karar geçerlidir; yalnızca `errors[].retryable` ise ve çağrı maliyetine değerse yeniden deneyin |

Yeniden denenen bir değerlendirme yeni bir değerlendirmedir: yeni bir `event_id` alır ve aylık
istek hakkınızdan düşer.
