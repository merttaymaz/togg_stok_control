#!/usr/bin/env python3
"""
Togg T10F V2 RWD Uzun Menzil stok takip botu.

Konfigüratör sayfasını headless Chromium ile açar, hem ekranda görünen metni
hem de sayfanın arka planda çektiği JSON yanıtlarını tarar. "Hızlı Teslim /
Hemen Teslim / Stoktan Teslim" bölümünde V2 RWD Uzun Menzil görünürse
Telegram'dan haber verir. Aynı aracı tekrar tekrar bildirmez.

Kullanım:
    python togg_stok_bot.py --test-telegram   # bildirim ayarını doğrula
    python togg_stok_bot.py --dump            # sayfa yapısını dump/ klasörüne yaz
    python togg_stok_bot.py --once            # tek kontrol (cron için)
    python togg_stok_bot.py                   # 5 dk'da bir sürekli kontrol
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_URL = "https://configurator.togg.com.tr/t10f"
# Konfigüratörün ürün listesini çektiği uç nokta (Playwright dump'ından tespit edildi)
DEFAULT_API_URL = (
    "https://bff.dfs.togg.cloud/smart-device-products/TUR/getSmartDeviceProducts/v1"
    "?collectionHandle=t10f-configurator"
)
DEFAULT_INTERVAL = 300  # saniye
ERROR_ALERT_THRESHOLD = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

log = logging.getLogger("togg-stok")


# ---------------------------------------------------------------------------
# Metin normalizasyonu ve eşleştirme kuralları
# ---------------------------------------------------------------------------

_TR_MAP = str.maketrans(
    {
        "İ": "i", "I": "i", "ı": "i",
        "Ş": "s", "ş": "s",
        "Ğ": "g", "ğ": "g",
        "Ü": "u", "ü": "u",
        "Ö": "o", "ö": "o",
        "Ç": "c", "ç": "c",
    }
)


def norm(s: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir (sadece eşleştirme için)."""
    return s.translate(_TR_MAP).lower()


# "Hızlı Teslim", "Hemen Teslim", "Stoktan Teslim", "Stok Araçlar" gibi başlıklar
STOCK_HEADING_RE = re.compile(r"\b(hizli|hemen|stoktan|stok|aninda)\b.*\b(teslim|arac)")
# Stok bölümünün bittiğini gösteren başlıklar
SECTION_END_RE = re.compile(
    r"^(konfigurator|konfigure et|versiyon sec|versiyonlar|sikca sorulan|iletisim|"
    r"t10x|t8x|t10f$)"
)
# Hedef araç (isimde "RWD" geçmeyebilir; 4More/AWD ayrıca dışlanıyor)
TARGET_RE = re.compile(r"\bv2\b.*(uzun menzil|long range)")
EXCLUDE_RE = re.compile(r"4more|awd|4x4")
# Aynı bölümdeki başka bir model satırı (yeni kart başlangıcı)
MODEL_LINE_RE = re.compile(r"\bt10f\b.*\bv[123]\b|\bv[123]\b.*\b(rwd|4more|awd)\b")
# Kart içindeki buton/eylem satırları (karta dahil etme)
ACTION_LINE_RE = re.compile(r"satin al|siparis|incele|detay|rezerv|sec$")

STOCK_KEY_RE = re.compile(
    r"stock|stok|quick|fast|hizli|ready|hemen|available|inventory|instant|aninda"
)


def is_target(s: str) -> bool:
    n = norm(s)
    return bool(TARGET_RE.search(n)) and not EXCLUDE_RE.search(n)


# ---------------------------------------------------------------------------
# 1) Sayfa metni üzerinden tespit
# ---------------------------------------------------------------------------


def find_stock_in_text(text: str) -> list[str]:
    """document.body.innerText içinde stok bölümündeki V2 RWD Uzun Menzil kartlarını bulur."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    matches: list[str] = []
    in_section = False
    i = 0
    while i < len(lines):
        line = lines[i]
        n = norm(line)
        if len(line) <= 60 and STOCK_HEADING_RE.search(n):
            in_section = True
            i += 1
            continue
        if in_section and SECTION_END_RE.search(n):
            in_section = False
            i += 1
            continue
        if in_section and is_target(line):
            card = [line]
            j = i + 1
            while j < len(lines) and len(card) < 4:
                nj = norm(lines[j])
                if (
                    MODEL_LINE_RE.search(nj)
                    or ACTION_LINE_RE.search(nj)
                    or STOCK_HEADING_RE.search(nj)
                    or SECTION_END_RE.search(nj)
                ):
                    break
                card.append(lines[j])
                j += 1
            matches.append(" | ".join(card))
            i = j
            continue
        i += 1
    return dedupe(matches)


# ---------------------------------------------------------------------------
# 2) Yakalanan JSON yanıtları üzerinden tespit
# ---------------------------------------------------------------------------


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return norm(v) in {"true", "1", "yes", "evet", "available", "instock", "in_stock"}
    return False


def _describe(item: dict) -> str:
    parts: list[str] = []
    for k, v in item.items():
        if isinstance(v, str) and 0 < len(v) <= 80:
            parts.append(v)
        elif isinstance(v, (int, float)) and re.search(r"price|fiyat|amount", k, re.I):
            parts.append(f"{v:,.0f} ₺".replace(",", "."))
        if len(parts) >= 6:
            break
    return " | ".join(parts)


def _walk(node: Any, container_is_stock: bool, out: list[str]) -> None:
    if isinstance(node, dict):
        strings = [v for v in node.values() if isinstance(v, str)]
        if any(is_target(s) for s in strings):
            flag_keys = [k for k in node if STOCK_KEY_RE.search(norm(str(k)))]
            if flag_keys:
                in_stock = any(_truthy(node[k]) for k in flag_keys)
            else:
                in_stock = container_is_stock
            if in_stock:
                out.append(_describe(node))
        for k, v in node.items():
            child_stock = container_is_stock or bool(STOCK_KEY_RE.search(norm(str(k))))
            _walk(v, child_stock, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, container_is_stock, out)


def find_stock_in_json(payloads: list[Any]) -> list[str]:
    """XHR/fetch JSON yanıtlarında stok işaretli V2 RWD Uzun Menzil kayıtlarını bulur."""
    out: list[str] = []
    for p in payloads:
        _walk(p, False, out)
    return dedupe(out)


# ---------------------------------------------------------------------------
# 3) API modu: getSmartDeviceProducts yanıtı
# ---------------------------------------------------------------------------


def _fmt_try(v: Any) -> str:
    return f"{v:,.0f} ₺".replace(",", ".")


def describe_product(p: dict) -> str:
    parts = [str(p.get("name") or p.get("type_name") or "?")]
    year = (p.get("model") or {}).get("year") if isinstance(p.get("model"), dict) else None
    if year:
        parts.append(str(year))
    prices = p.get("price") or {}
    price_bits = []
    if isinstance(prices, dict):
        for key in sorted(prices):
            m = re.fullmatch(r"sct_(\d+)", key)
            val = prices[key].get("with_all_taxes") if isinstance(prices[key], dict) else None
            if m and isinstance(val, (int, float)):
                price_bits.append(f"ÖTV %{m.group(1)}: {_fmt_try(val)}")
    if price_bits:
        parts.append(", ".join(price_bits))
    return " | ".join(parts)


def find_target_products(payload: Any) -> list[str]:
    """getSmartDeviceProducts yanıtındaki ürünler arasında V2 (RWD) Uzun Menzil olanları döndürür."""
    if not isinstance(payload, dict):
        return []
    products = payload.get("products")
    if not isinstance(products, list):
        return []
    out = []
    for p in products:
        if not isinstance(p, dict):
            continue
        names = [str(p.get(k)) for k in ("name", "type_name") if p.get(k)]
        if any(is_target(n) for n in names):
            out.append(describe_product(p))
    return dedupe(out)


def fetch_products(api_url: str, timeout: int = 30) -> Any:
    import requests

    r = requests.get(
        api_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Origin": "https://configurator.togg.com.tr",
            "Referer": "https://configurator.togg.com.tr/",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            result.append(it)
    return result


def new_matches(old: list[str], new: list[str]) -> list[str]:
    """Daha önce görülmemiş araçları döndürür (stok azalınca bildirim yok)."""
    seen = set(old)
    return [m for m in new if m not in seen]


def parse_env_file(content: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def load_env(path: Path) -> None:
    if path.exists():
        for k, v in parse_env_file(path.read_text(encoding="utf-8")).items():
            os.environ.setdefault(k, v)


# ---------------------------------------------------------------------------
# Durum dosyası
# ---------------------------------------------------------------------------


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("state dosyası bozuk, sıfırlanıyor: %s", path)
    return {"last_matches": [], "consecutive_errors": 0, "error_notified": False}


def save_state(path: Path, state: dict) -> None:
    state["last_check"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def send_telegram(text: str) -> None:
    import requests

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID tanımlı olmalı (.env)")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    r.raise_for_status()


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_stock_message(matches: list[str], url: str) -> str:
    lines = "\n".join(f"• {html_escape(m)}" for m in matches)
    return (
        "🚗 <b>Togg T10F V2 RWD Uzun Menzil stokta!</b>\n\n"
        f"{lines}\n\n"
        f'<a href="{url}">Konfigüratöre git</a>'
    )


# ---------------------------------------------------------------------------
# Sayfayı çekme (Playwright)
# ---------------------------------------------------------------------------


def fetch_page(url: str, timeout_ms: int = 60_000, dump_dir: Path | None = None):
    """Sayfa metnini ve yakalanan JSON yanıtlarını döndürür: (text, payloads, endpoints)."""
    from playwright.sync_api import sync_playwright

    responses = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="tr-TR",
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        page.on("response", lambda resp: responses.append(resp))
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            log.debug("networkidle beklenirken zaman aşımı, devam ediliyor")
        # Çerez bandı varsa kapat
        try:
            page.get_by_role(
                "button", name=re.compile(r"kabul|accept|tamam|onayla", re.I)
            ).first.click(timeout=2_000)
        except Exception:
            pass
        page.wait_for_timeout(3_000)
        # Lazy-load bölümleri tetiklemek için sayfayı aşağı kaydır
        try:
            page.mouse.wheel(0, 20_000)
            page.wait_for_timeout(1_500)
        except Exception:
            pass
        text = page.evaluate("() => document.body.innerText")
        if dump_dir:
            page.screenshot(path=str(dump_dir / "page.png"), full_page=True)

        payloads, endpoints = [], []
        for resp in responses:
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct:
                continue
            try:
                payloads.append(resp.json())
                endpoints.append(resp.url)
            except Exception:
                continue
        browser.close()
    return text, payloads, endpoints


def write_dump(dump_dir: Path, text: str, payloads: list, endpoints: list[str]) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    (dump_dir / "page.txt").write_text(text, encoding="utf-8")
    (dump_dir / "endpoints.txt").write_text("\n".join(endpoints), encoding="utf-8")
    for i, (u, p) in enumerate(zip(endpoints, payloads)):
        (dump_dir / f"json_{i:02d}.json").write_text(
            json.dumps({"url": u, "body": p}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    log.info("Dump yazıldı: %s (page.txt, page.png, %d JSON)", dump_dir, len(payloads))


# ---------------------------------------------------------------------------
# Kontrol döngüsü
# ---------------------------------------------------------------------------


def check_once(
    url: str,
    state: dict,
    dump_dir: Path | None = None,
    mode: str = "api",
    api_url: str = DEFAULT_API_URL,
) -> list[str]:
    if mode == "api":
        payload = fetch_products(api_url)
        names = [
            str(p.get("name")) for p in (payload.get("products") or []) if isinstance(p, dict)
        ] if isinstance(payload, dict) else []
        log.info("Konfigüratördeki ürünler: %s", names or "(boş)")
        if dump_dir:
            dump_dir.mkdir(parents=True, exist_ok=True)
            (dump_dir / "products.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log.info("Dump yazıldı: %s/products.json", dump_dir)
        matches = find_target_products(payload)
    else:
        text, payloads, endpoints = fetch_page(url, dump_dir=dump_dir)
        if dump_dir:
            write_dump(dump_dir, text, payloads, endpoints)
        matches = dedupe(find_stock_in_text(text) + find_stock_in_json(payloads))
    fresh = new_matches(state.get("last_matches", []), matches)
    if fresh:
        log.info("YENİ STOK: %s", fresh)
        send_telegram(format_stock_message(fresh, url))
    elif matches:
        log.info("Stok var (daha önce bildirildi): %d araç", len(matches))
    else:
        log.info("Stok yok")
    state["last_matches"] = matches
    return matches


def run(
    url: str,
    interval: int,
    once: bool,
    state_path: Path,
    dump_dir: Path | None,
    max_runtime: int | None = None,
    mode: str = "api",
    api_url: str = DEFAULT_API_URL,
) -> int:
    """max_runtime verilirse (saniye) bir sonraki kontrol bu süreyi aşacaksa döngüden çıkar."""
    state = load_state(state_path)
    started = time.monotonic()
    while True:
        try:
            check_once(url, state, dump_dir, mode=mode, api_url=api_url)
            if state.get("consecutive_errors"):
                log.info("Kontrol tekrar başarılı")
            state["consecutive_errors"] = 0
            state["error_notified"] = False
        except Exception as exc:  # noqa: BLE001
            if dump_dir:
                raise  # dump modunda hatayı gizleme, çalıştırma başarısız olsun
            state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
            log.exception("Kontrol hatası (%d. ardışık)", state["consecutive_errors"])
            if (
                state["consecutive_errors"] >= ERROR_ALERT_THRESHOLD
                and not state.get("error_notified")
            ):
                try:
                    send_telegram(
                        "⚠️ Togg stok botu üst üste "
                        f"{state['consecutive_errors']} kontrolde hata aldı:\n"
                        f"<code>{html_escape(str(exc))[:500]}</code>"
                    )
                    state["error_notified"] = True
                except Exception:  # noqa: BLE001
                    log.exception("Hata bildirimi de gönderilemedi")
        save_state(state_path, state)
        if once:
            return 0
        if max_runtime is not None and (time.monotonic() - started) + interval > max_runtime:
            log.info("Maksimum çalışma süresi doldu, çıkılıyor")
            return 0
        log.debug("%d sn bekleniyor", interval)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Togg T10F V2 RWD Uzun Menzil stok takip botu")
    parser.add_argument("--mode", choices=["api", "browser"], default=None,
                        help="api: ürün JSON uç noktasını doğrudan sorgula (varsayılan, hafif); "
                             "browser: Playwright ile sayfayı aç")
    parser.add_argument("--api-url", default=None, help=f"API modunda sorgulanacak adres (varsayılan: {DEFAULT_API_URL})")
    parser.add_argument("--url", default=None, help=f"Browser modunda açılacak sayfa (varsayılan: {DEFAULT_URL})")
    parser.add_argument("--interval", type=int, default=None, help="Kontrol aralığı, saniye (varsayılan 300)")
    parser.add_argument("--once", action="store_true", help="Tek kontrol yapıp çık (cron için)")
    parser.add_argument("--max-runtime", type=int, default=None, help="Bu kadar saniye sonra döngüden çık (GitHub Actions için)")
    parser.add_argument("--dump", action="store_true", help="Sayfa metni, ekran görüntüsü ve JSON yanıtlarını dump/ klasörüne yaz")
    parser.add_argument("--test-telegram", action="store_true", help="Telegram'a test mesajı gönder ve çık")
    parser.add_argument("--state", default="state.json", help="Durum dosyası yolu")
    parser.add_argument("--env", default=".env", help=".env dosyası yolu")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_env(Path(args.env))

    if args.test_telegram:
        try:
            send_telegram("✅ Togg stok botu bağlantı testi başarılı.")
        except Exception as exc:  # noqa: BLE001
            log.error("Telegram testi başarısız: %s", exc)
            return 1
        log.info("Test mesajı gönderildi")
        return 0

    url = args.url or os.environ.get("TOGG_URL") or DEFAULT_URL
    api_url = args.api_url or os.environ.get("TOGG_API_URL") or DEFAULT_API_URL
    mode = args.mode or os.environ.get("CHECK_MODE") or "api"
    interval = args.interval or int(os.environ.get("CHECK_INTERVAL", DEFAULT_INTERVAL))
    dump_dir = Path("dump") if args.dump else None
    try:
        return run(
            url, interval, args.once or args.dump, Path(args.state), dump_dir,
            args.max_runtime, mode=mode, api_url=api_url,
        )
    except KeyboardInterrupt:
        log.info("Durduruldu")
        return 0


if __name__ == "__main__":
    sys.exit(main())
