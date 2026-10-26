# ScriptForge

Masaüstü penceresi: ne istediğini yaz → LLM (Gemini veya OpenRouter) Python scriptini yazar → çalıştırır →
hata olursa hatayı LLM'e geri verip düzelttirir → başarılı olana (veya deneme limitine) kadar tekrarlar.
Tek tık.

## Kurulum
```bash
# Linux: sudo apt install python3-tk  (ek pip paketi gerekmez)
export GEMINI_API_KEY=...       # https://aistudio.google.com/apikey
export OPENROUTER_API_KEY=...   # https://openrouter.ai/keys (opsiyonel)
python -m scriptforge
```

## Özellikler
- **Düzenleme (Kitaplık):** sağ paneldeki kod doğrudan düzenlenir (💾 Kaydet; "Çalıştır" önce otomatik kaydeder). "✨ LLM ile değiştir" ile "şunu da ekle" gibi bir talimat verirsin; LLM mevcut kodu değiştirir, yine çalıştırıp hata varsa debug eder. Her değişiklikte eski sürüm `history/` altına yedeklenir, "↶ Önceki sürüm" ile geri alınır.
- **Kitaplık sekmesi:** üretilen tüm scriptler listelenir (tarih, istek, ✔/✘ durumu), arama yapılır; çift tıkla ya da "Çalıştır" ile LLM kullanmadan tekrar çalıştırılır. Kodu görme, kopyalama, klasörü açma ve silme de var.
- Sağlayıcı seçimi: Gemini veya OpenRouter. OpenRouter'da "Ücretsiz modelleri getir" ile `:free` modeller listelenir, açılır kutudan seçilir (`openrouter/free` otomatik ücretsiz model seçer). Ücretsiz modellerin kalitesi/limiti değişkendir.
- Üret → çalıştır → hata çıktısıyla otomatik debug döngüsü (varsayılan 4 deneme)
- Script `# requirements:` satırı yazarsa paketler `~/ScriptForge/libs` içine kurulur
- Canlı çıktı + etkileşimli girdi: script `input()` isterse pencerenin altındaki "Script'e girdi" kutusuna yazıp Enter'a bas (EOF butonu stdin'i kapatır). Girdi isteyen scriptlerde zaman aşımını 0 (sınırsız) yap.
- Zaman aşımı, Durdur butonu, "çalıştırmadan önce onayla" seçeneği
- Her çalıştırma `~/ScriptForge/scripts/<tarih>/script.py` olarak saklanır (`SCRIPTFORGE_HOME` ile değişir)

## Güvenlik
Üretilen kod **senin kullanıcı yetkinle** çalışır; sandbox yoktur. Güvenmediğin istekler için
"çalıştırmadan önce onayla" kutusunu işaretle.

## Test
```bash
pip install pytest && pytest
```
