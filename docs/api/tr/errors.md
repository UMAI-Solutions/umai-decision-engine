# Hatalar

Her hata sabit bir **kod** taşır. Programınızı mesaja değil koda göre yazın: mesajların
ifadesi değişebilir. Hiçbir hata mesajı içeriğinizi içermez.

## Hata biçimleri

Değerlendirme API'si hataları şöyle yanıtlar:

```json
{
  "detail": {
    "error": "invalid_credential",
    "detail": "missing or invalid API credential"
  }
}
```

Bazı kodlar alan ekler: `input_too_large` (`limit`, `size`, `unit`), `quota_exceeded` (`limit`,
`used`, `environment`, `resets_at`) ve `invalid_request` (`problems`: isteğin nerede ve nasıl
hatalı olduğu, ne söylediği asla değil):

```json
{
  "detail": {
    "error": "invalid_request",
    "detail": "the request does not match the evaluation API contract",
    "problems": [
      { "loc": ["body", "contract_version"], "type": "literal_error" },
      { "loc": ["body", "<key>"], "type": "extra_forbidden" }
    ]
  }
}
```

API'nin parçası olmayan bir alan adı `<key>` olarak gösterilir.

AI Gateway OpenAI'nin hata biçiminde yanıt verir, böylece OpenAI SDK'ları alışılmış
istisnalarını fırlatır. `type`, aşağıdaki hatalar için `umai_gateway`, guardrail'inizin
reddetmeleri için `umai_guardrail` olur ([AI Gateway](gateway.md#reddetmeler)); bir alan söz
konusuysa `param` onu adlandırır:

```json
{
  "error": {
    "message": "this key may not be used from this address",
    "type": "umai_gateway",
    "code": "ip_not_allowed"
  }
}
```

Model sağlayıcınızdan gelen bir hata, `X-Umai-Upstream-Error: true` yanıt başlığıyla
değiştirilmeden iletilir.

## Değerlendirme API

| HTTP | Kod | Anlamı | Çözüm |
|---|---|---|---|
| 400 | `tenant_required` | Yalnızca kendi sunucunuzda: operatör belirteci `tenant_id` olmadan kullanıldı | `tenant_id` gönderin ya da bir API anahtarı kullanın |
| 401 | `invalid_credential` | Anahtar eksik, bilinmiyor, süresi dolmuş ya da iptal edilmiş | Etkin bir anahtarla `Authorization: Bearer <anahtar>` gönderin; aynı anahtarla yeniden denemeyin |
| 403 | `ip_not_allowed` | Anahtar, isteğinizin gelmediği ağlarla sınırlı | İzin verilen bir ağdan çağırın ya da çıkış adresinizi anahtara ekleyin ([ayrıntılar](authentication.md#izin-verilen-aglar)) |
| 403 | `insufficient_scope` | Anahtarda `evaluate` kapsamı yok | Bu kapsama sahip bir uygulama anahtarı kullanın |
| 403 | `tenant_mismatch` | `tenant_id`, anahtarınkinden başka bir kuruluşu belirtiyor | `tenant_id` göndermeyin |
| 403 | `guardrail_mismatch` | Anahtar bir uygulamaya ait ve istek başka bir guardrail ya da sürüm belirtiyor | `bundle` göndermeyin |
| 403 | `unknown_application` | Anahtarın uygulaması devre dışı ya da silinmiş | Uygulamayı etkinleştirin ya da etkin bir uygulamanın anahtarını kullanın |
| 413 | `input_too_large` | İçerik sınırınızdan uzun; hiçbir şey değerlendirilmedi | Daha azını gönderin: yalnızca denetlenmesi gerekeni, uzun belgeleri parçalar hâlinde ([Sınırlar](limits.md#icerik-boyutu)) |
| 422 | `invalid_request` | Gövde API'ye uymuyor: eksik ya da bilinmeyen bir alan, yanlış bir tür ya da değer | `problems` alanını okuyun: `loc` alanı, `type` sorunu söyler. `contract_version` `"0.12"` ya da kabul edilen daha eski bir sürüm olmalıdır |
| 422 | `bundle_required` | Anahtar hiçbir uygulamaya ait değil, bu yüzden istek bir guardrail belirtmeli | `bundle.id` gönderin ya da bir uygulamanın anahtarını kullanın |
| 429 | `rate_limited` | Bu anahtar için çok fazla istek | `Retry-After` saniye bekleyin; yükünüzü yayın |
| 429 | `too_many_failed_attempts` | Adresinizden çok fazla geçersiz anahtar geldi; oradan gelen her anahtar şimdilik reddediliyor | Anahtarı düzeltin, 401'leri yeniden denemeyi bırakın, `Retry-After` kadar bekleyin |
| 429 | `quota_exceeded` | Kuruluşunuz bu ayın istek hakkını kullandı | `resets_at` zamanını bekleyin ya da planınız için bizimle iletişime geçin |
| 503 | — | Hizmet başlıyor | Birkaç saniye sonra yeniden deneyin |

## AI Gateway

| HTTP | Kod | Anlamı | Çözüm |
|---|---|---|---|
| 400 | `umai_blocked` | Guardrail'iniz çağrıyı reddetti; `umai.phase` hangi denetim olduğunu söyler | Aynı içeriği yeniden denemeyin; kullanıcınıza kendi mesajınızı gösterin |
| 400 | `umai_step_up_required` | Guardrail'iniz, bir API çağrısının veremeyeceği bir onay istiyor | Reddetme olarak ele alın ya da içeriği kendi onay akışınızla denetleyin |
| 400 | `invalid_request` | Gövde JSON değil, nesne değil ya da `model`, `messages` veya `input` eksik; ya da `model` bir model adında bulunamayacak karakterler içeriyor | `param` içinde adı geçen alanı düzeltin |
| 400 | `application_required` | Anahtar hiçbir uygulamaya ait değil ve kuruluşunuzun birden fazla uygulaması var | `X-Umai-Application` gönderin ya da bir uygulamanın anahtarını kullanın |
| 400 | `provider_key_required` | Sağlayıcı sizin anahtarınızla bağlı ve çağrı onu göndermedi | Sağlayıcı anahtarınızı `X-Umai-Provider-Key` başlığında gönderin |
| 400 | `unsupported_content` | Guardrail'in denetleyemediği içerik: görseller, ses, dosyalar, token dizileri, `prediction`, ses çıktısı, `prompt.variables` | Metin gönderin ya da bir yöneticiden uygulamanın bu parçaları denetlenmeden iletmesine izin vermesini isteyin |
| 401 | `invalid_credential` | UMAI anahtarı eksik, bilinmiyor, süresi dolmuş ya da iptal edilmiş | SDK'nın API anahtarı olarak etkin bir UMAI anahtarı kullanın (sağlayıcı anahtarınızı değil) |
| 403 | `ip_not_allowed` | Anahtar, isteğinizin gelmediği ağlarla sınırlı | İzin verilen bir ağdan çağırın ya da çıkış adresinizi anahtara ekleyin |
| 403 | `insufficient_scope` | Anahtarda `gateway` kapsamı yok | Bir uygulamanın anahtarını kullanın |
| 403 | `application_mismatch` | `X-Umai-Application`, anahtarınkinden başka bir uygulamayı belirtiyor | Başlığı göndermeyin |
| 403 | `provider_not_allowed` | Uygulama o sağlayıcıyı kullanamaz ya da sağlayıcının adresine bu hizmette izin verilmiyor | İzin verilen bir sağlayıcının modelini kullanın; bir yöneticiye danışın |
| 404 | `unknown_application` | Uygulama devre dışı, silinmiş ya da bilinmiyor | Uygulamayı konsolda kontrol edin |
| 404 | `unknown_provider` | `@<provider>/` bağlı bir sağlayıcıyı adlandırmıyor ya da varsayılan sağlayıcı yok | Model adını düzeltin ya da sağlayıcıyı bağlayın |
| 404 | `model_not_allowed` | Uygulama yalnızca listelenen modelleri kabul ediyor | `GET /v1/models` içindeki bir adı kullanın |
| 413 | `request_too_large` | Gövde izin verilenden büyük | Daha kısa bir konuşma ya da daha az ek gönderin |
| 413 | `input_too_large` | Denetlenecek metin izin verilenden uzun | Konuşmayı kısaltın |
| 429 | `rate_limited` | Bu anahtar için çok fazla istek | `Retry-After` saniye bekleyin |
| 429 | `too_many_failed_attempts` | Adresinizden çok fazla geçersiz anahtar geldi | Anahtarı düzeltin, 401'leri yeniden denemeyi bırakın, `Retry-After` kadar bekleyin |
| 429 | `quota_exceeded` | Kuruluşunuz bu ayın istek hakkını kullandı | Gelecek aya kadar yeniden denemeyi bırakın (SDK yeniden dener) ya da bizimle iletişime geçin |
| 500 | `provider_misconfigured` | Sağlayıcının anahtarı UMAI tarafında kurulu değil | Yöneticinize ya da bize başvurun |
| 502 | `upstream_unreachable` | Sağlayıcıya ulaşılamadı | Geri çekilmeyle yeniden deneyin |
| 502 | `upstream_invalid_response` | Sağlayıcının yanıtı geçerli JSON değildi | Yeniden deneyin; sürerse sağlayıcıyı kontrol edin |
| 504 | `upstream_timeout` | Sağlayıcı zamanında yanıt vermedi | Geri çekilmeyle yeniden deneyin ya da isteği kısaltın |

Bir akış sırasında ağ geçidi, durum kodu yerine bir hata olayıyla bitebilir: `code`
`upstream_timeout` (sağlayıcı yanıt vermeyi bıraktı) ya da `upstream_invalid_response`.

## Karar içindeki hatalar

Bir değerlendirmenin bir kısmı başarısız olduğunda yanıt yine bir karardır (HTTP 200; ağ
geçidinde çağrı o kararın söylediği gibi devam eder ya da reddedilir), `degraded` `true` olur ve
`errors` neyin başarısız olduğunu listeler. Karar zaten guardrail'in hata politikasına uyar.

| Tür | Anlamı | Çözüm |
|---|---|---|
| `CONFIG_MISSING` | Guardrail (ya da belirtilen sürüm) kullanılamıyor | Guardrail kimliğini ve bir sürümün yayınlanıp etkin olduğunu kontrol edin |
| `CONFIG_INVALID` | Guardrail bu aşamayı etkinleştirmiyor ya da yüklenemiyor | Aşamayı guardrail'de etkinleştirin ya da etkinleştirdiği bir aşamayla çağırın |
| `TIMEOUT` | Varlık tespiti süre bütçesi içinde bitmedi | Yeniden deneyin; `timeout_ms` değerini artırın; daha az içerik gönderin |
| `L0_ERROR` | Varlık tespiti başarısız oldu | Yeniden deneyin; sürerse `event_id` ile bize ulaşın |
| `TENANT_KEY_MISSING` | Kuruluşunuzun anahtar malzemesi bu hizmette mevcut değil | Bize ulaşın |
| `L1_NOT_IMPLEMENTED` | Guardrail'de UMAI'nin bu sürümünün henüz değerlendiremediği kurallar var | Bize ulaşın |
| `L1B_TIMEOUT` | Bir karar modeli politikası zamanında yanıt vermedi | Yeniden deneyin ya da guardrail'in karar modeli zaman aşımını artırın |
| `L1B_RATE_LIMITED` | Karar modeli sağlayıcısı çağrıları sınırladı | Daha sonra yeniden deneyin |
| `L1B_UNAVAILABLE` | Karar modeli sağlayıcısı kullanılamıyor ya da yapılandırılmamış | Daha sonra yeniden deneyin; sürerse bize ulaşın |
| `L1B_INVALID_RESPONSE` | Karar modeli kullanılamaz bir yanıt verdi | `event_id` ile bize ulaşın |
| `L1B_AUTH` | Karar modeli sağlayıcısı UMAI'nin kimlik bilgilerini reddetti | Bize ulaşın |
