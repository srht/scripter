# ScriptForge

Masaüstü penceresi: ne istediğini yaz → LLM (Google Gemini) Python scriptini yazar → çalıştırır →
hata olursa hatayı LLM'e geri verip düzelttirir → başarılı olana (veya deneme limitine) kadar tekrarlar.
Tek tık.

## Kurulum
```bash
# Linux: sudo apt install python3-tk  (ek pip paketi gerekmez)
export GEMINI_API_KEY=...   # https://aistudio.google.com/apikey
python -m scriptforge
```

## Özellikler
- Üret → çalıştır → hata çıktısıyla otomatik debug döngüsü (varsayılan 4 deneme)
- Script `# requirements:` satırı yazarsa paketler `~/ScriptForge/libs` içine kurulur
- Zaman aşımı, Durdur butonu, "çalıştırmadan önce onayla" seçeneği
- Her çalıştırma `~/ScriptForge/scripts/<tarih>/script.py` olarak saklanır (`SCRIPTFORGE_HOME` ile değişir)

## Güvenlik
Üretilen kod **senin kullanıcı yetkinle** çalışır; sandbox yoktur. Güvenmediğin istekler için
"çalıştırmadan önce onayla" kutusunu işaretle.

## Test
```bash
pip install pytest && pytest
```
