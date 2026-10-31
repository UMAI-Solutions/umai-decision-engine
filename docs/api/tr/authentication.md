# Kimlik doğrulama

`/v1` altındaki her çağrı bir UMAI API anahtarı taşır:

```text
Authorization: Bearer umai_live_eu_Q7h2kP9c_<secret>
```

Anahtarı yalnızca bu başlıkta gönderin, asla bir URL'de değil. Eksik, bilinmeyen, süresi dolmuş
ya da iptal edilmiş bir anahtar **401** `invalid_credential` ile yanıtlanır.

## API anahtarları

Anahtarları konsolda **Uygulamalar** altında, anahtarın ait olacağı uygulamada oluşturun. Konsol
anahtarı yalnızca oluşturduğunuz anda bir kez gösterir; UMAI gizli kısmın yalnızca özetini
(hash) saklar, bu yüzden kaybolan bir anahtar yeniden gösterilemez: yenisini oluşturun.

```text
umai_<environment>_<region>_<key id>_<secret>
```

| Bölüm | Değerler | Anlamı |
|---|---|---|
| environment | `live`, `test` | Canlı ya da test. Test anahtarlarının kendi, daha küçük aylık istek hakkı vardır; entegrasyon sırasında bunları kullanın |
| region | `eu`, `us`, `sh` | Anahtarın oluşturulduğu bölge; `sh` kendi sunucunuzda bir kurulumdur |
| key id | 8 harf ve rakam | Gizli değildir: konsolda ve kayıtlarda görünür |
| secret | 43 harf ve rakam | Gizli kısım |

Anahtarları sunucularınızda, gizli bilgi deponuzda ya da ortam değişkenlerinde tutun.
Tarayıcıdaki, mobil uygulamadaki ya da bir depodaki (repository) anahtarı onu gören herkes
kopyalayabilir.

## Bir anahtar neyi çağırabilir

Bir anahtar **kapsamlar** (scopes) taşır:

| Kapsam | İzin verdiği |
|---|---|
| `evaluate` | `POST /v1/evaluate` |
| `gateway` | AI Gateway: `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/models` |

Bir uygulama için oluşturulan anahtar ikisine de sahiptir. Bir uç noktanın gerektirdiği kapsamı
taşımayan anahtar **403** `insufficient_scope` ile yanıtlanır.

## Anahtarlar ve uygulamalar

Bir anahtar tek bir **uygulamaya** aittir ve hangi guardrail'in uygulanacağına uygulama karar
verir:

- Değerlendirme API'sinde istekte `bundle` alanını göndermeyin; uygulamanın guardrail'i
  kullanılır. Başka bir guardrail belirtmek **403** `guardrail_mismatch` ile reddedilir.
- AI Gateway'de anahtar uygulamayı seçer; başka bir uygulamayı belirten `X-Umai-Application`
  başlığı **403** `application_mismatch` ile reddedilir.
- Uygulama devre dışı bırakıldıysa ya da silindiyse anahtarları `unknown_application` ile
  reddedilir (değerlendirme API'sinde **403**, ağ geçidinde **404**).

Hiçbir uygulamaya ait olmayan bir anahtar, değerlendirme API'sinde guardrail'i `bundle.id`
alanında belirtmelidir (aksi hâlde **422** `bundle_required`); kuruluşun birden fazla uygulaması
varsa ağ geçidinde uygulamayı `X-Umai-Application` başlığında belirtmelidir (aksi hâlde **400**
`application_required`).

## İzin verilen ağlar

Bir sahip ya da yönetici, bir anahtarı sunucularınızın çağrı yaptığı ağlarla sınırlayabilir
(IPv4 ve IPv6 aralıkları, en fazla 50, konsoldaki anahtar ayarlarında). Aynı anahtar başka bir
adresten kullanıldığında, hiçbir şey denetlenmeden **403** `ip_not_allowed` ile yanıtlanır:

```json
{
  "detail": {
    "error": "ip_not_allowed",
    "detail": "this key may not be used from this address"
  }
}
```

UMAI trafiğinizin çıktığı adresi görür: NAT, bir vekil sunucu (proxy) ya da bulut çıkış ağ
geçidinin arkasında bu, sunucunuzun özel adresi değil o cihazın genel adresidir. Beklemediğiniz
bir `ip_not_allowed` alırsanız çıkış adresinizi kontrol edin ve aralığını anahtara ekleyin.

## Çok fazla geçersiz anahtar

Çok fazla geçersiz anahtar (60 saniyede 20) gönderen bir adres bir süre reddedilir: o adresten
gelen her istek, geçerli bir anahtarla bile, `Retry-After` başlığıyla **429**
`too_many_failed_attempts` alır ve anahtar kontrol edilmez. Bir IPv6 adresi tüm /64 ağıyla
birlikte sayılır.

Bu, anahtarlarınızı tahmin edilmeye karşı korur. Aynı zamanda yanlış ya da iptal edilmiş bir
anahtarla yeniden deneyen bir kurulumun, aynı adresin arkasındaki komşularını da
engelleyebileceği anlamına gelir. Bir 401'i yeniden denemeyin: anahtarı düzeltin, sonra
`Retry-After` kadar bekleyin.

## Anahtarları yenilemek ve iptal etmek

1. Uygulama için yeni bir anahtar oluşturun.
2. Yeni anahtarı sunucularınıza dağıtın.
3. Eski anahtarı konsolda iptal edin.

İptal edilen bir anahtar kısa süre içinde çalışmayı bırakır; iptalden sonra ona güvenmeyin. Bir
anahtar sızmış olabilirse önce iptal edin, sonra yenisini oluşturun. Bir anahtarı iptal etmek
uygulamanın diğer anahtarlarını etkilemez.
