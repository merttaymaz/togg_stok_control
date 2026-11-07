# Togg T10F V2 RWD Uzun Menzil – Stok Takip Botu

Konfigüratör sayfasını 5 dakikada bir headless Chromium ile açar; "Hızlı Teslim /
Hemen Teslim / Stoktan Teslim" bölümünde **V2 RWD Uzun Menzil** görünürse Telegram'a
mesaj atar. Aynı aracı bir kez bildirir, stok yokken sessiz kalır, üst üste 3 hata
alırsa tek bir uyarı gönderir.

## Kurulum (Linux / macOS / Windows)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium   # Linux'ta ilk seferde: python -m playwright install --with-deps chromium
cp .env.example .env               # token'ı doldur
```

## Çalıştırma

```bash
python togg_stok_bot.py --test-telegram   # 1) Telegram'a test mesajı gelmeli
python togg_stok_bot.py --dump            # 2) sayfa yapısını dump/ klasörüne yazar (ilk kurulumda bir kez)
python togg_stok_bot.py --once            # 3) tek kontrol
python togg_stok_bot.py                   # 4) sürekli: her 5 dk (CHECK_INTERVAL)
```

Sürekli çalıştırmak için `togg-stok-bot.service` (systemd) kullan ya da cron'a
`*/5 * * * * cd /yol/togg-stok-bot && .venv/bin/python togg_stok_bot.py --once >> bot.log 2>&1`
satırını ekle.

## GitHub Actions'ta çalıştırma (sunucusuz, ücretsiz)

1. GitHub'da **public** bir repo aç (public repolarda Actions dakikası sınırsız; private'ta
   aylık 2.000 dk sürekli çalışmaya yetmez). Repoda gizli bir şey yok; token Secrets'ta durur.
2. Bu klasörün içeriğini repoya yükle (`.env` dosyasını yükleme — zaten `.gitignore`'da).
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` ekle.
4. **Actions** sekmesinde "Togg stok kontrol" workflow'unu seç → **Run workflow**,
   `dump` kutusunu işaretleyip çalıştır → biten işin altındaki `togg-dump` artifact'ini indir
   (eşleştirme kuralını kesinleştirmek için).
5. Bir daha **Run workflow** (dump işaretsiz) de; artık her 30 dk'da bir iş başlar ve
   ~55 dk boyunca 4 dk'da bir kontrol eder. Yeni iş eskisini iptal ettiği için aralık
   kesintisiz ~4 dk'dır. Loglar Actions sekmesinde görünür.

Durdurmak için: Actions → workflow → "⋯" → **Disable workflow**.

## İlk kurulumda `--dump` neden önemli?

Togg'un sayfası JavaScript ile yüklenen bir uygulama; stok verisinin tam olarak hangi
başlık altında / hangi JSON uç noktasında geldiği dışarıdan görülemiyor. `--dump`
şunları `dump/` klasörüne yazar:

- `page.txt` – sayfada görünen tüm metin
- `page.png` – tam sayfa ekran görüntüsü
- `endpoints.txt` + `json_XX.json` – sayfanın arka planda çektiği JSON yanıtları

Bu çıktıya bakarak eşleştirme kurallarını (`STOCK_HEADING_RE`, `TARGET_RE`,
`STOCK_KEY_RE`) kesinleştirmek mümkün. Stok verisi temiz bir JSON uç noktasından
geliyorsa Playwright'a gerek kalmaz; bot doğrudan o adresi sorgulayacak şekilde
sadeleştirilebilir.

## Testler

```bash
python -m pytest -q
```

## Dosyalar

- `togg_stok_bot.py` – botun tamamı
- `tests/test_parse.py` – eşleştirme ve durum mantığı testleri
- `.env.example` – ayar şablonu
- `togg-stok-bot.service` – systemd servis dosyası
- `state.json` – (çalışınca oluşur) son görülen stok ve hata sayacı
