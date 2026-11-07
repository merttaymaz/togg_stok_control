from togg_stok_bot import (
    find_stock_in_json,
    find_stock_in_text,
    new_matches,
    parse_env_file,
)

# ---------------------------------------------------------------------------
# Sayfa metni (document.body.innerText) üzerinden tespit
# ---------------------------------------------------------------------------

VERSION_LIST_ONLY = """
T10F
Versiyon seç
T10F V1 RWD Standart Menzil
350 km Menzil • 160 kW • Arkadan itişli
1.860.000 ₺
T10F V2 RWD Uzun Menzil
610 km Menzil • 160 kW • Arkadan itişli
2.907.800 ₺
T10F V2 4More
Tahmini Teslimat Tarihi: Aralık
"""

WITH_STOCK_SECTION = VERSION_LIST_ONLY + """
Hızlı Teslim Araçlar
T10F V2 RWD Uzun Menzil
Urla Yeşil • Cam Tavan
2.907.800 ₺
Hemen Satın Al
T10F V1 RWD Standart Menzil
Anadolu Gri
1.860.000 ₺
Konfigüratör
"""


def test_version_list_alone_is_not_stock():
    assert find_stock_in_text(VERSION_LIST_ONLY) == []


def test_v2_rwd_under_quick_delivery_heading_is_stock():
    matches = find_stock_in_text(WITH_STOCK_SECTION)
    assert len(matches) == 1
    assert "V2 RWD Uzun Menzil" in matches[0]
    assert "Urla Yeşil" in matches[0]


def test_4more_under_quick_delivery_heading_is_ignored():
    text = "Hemen Teslim\nT10F V2 4More Uzun Menzil\nSiyah\n3.100.000 ₺\n"
    assert find_stock_in_text(text) == []


def test_heading_match_is_case_and_turkish_i_insensitive():
    text = "HIZLI TESLİM\nT10F V2 RWD UZUN MENZİL\nBeyaz\n"
    assert len(find_stock_in_text(text)) == 1


def test_stock_section_ends_at_next_heading():
    text = (
        "Hızlı Teslim\n"
        "T10F V1 RWD Standart Menzil\nGri\n"
        "Konfigüratör\n"
        "T10F V2 RWD Uzun Menzil\n610 km\n"
    )
    assert find_stock_in_text(text) == []


# ---------------------------------------------------------------------------
# Yakalanan JSON yanıtları üzerinden tespit
# ---------------------------------------------------------------------------


def test_json_stock_item_with_matching_name_is_found():
    payload = {
        "stockVehicles": [
            {"name": "T10F V2 RWD Uzun Menzil", "color": "Urla Yeşil", "price": 2907800},
            {"name": "T10F V2 4More", "color": "Siyah", "price": 3100000},
        ]
    }
    matches = find_stock_in_json([payload])
    assert len(matches) == 1
    assert "V2 RWD Uzun Menzil" in matches[0]
    assert "Urla Yeşil" in matches[0]


def test_json_version_catalog_without_stock_key_is_ignored():
    payload = {
        "versions": [
            {"name": "T10F V2 RWD Uzun Menzil", "range": 610, "price": 2907800},
        ]
    }
    assert find_stock_in_json([payload]) == []


def test_json_item_with_truthy_stock_flag_is_found():
    payload = {"versions": [{"title": "V2 RWD Long Range", "inStock": True}]}
    assert len(find_stock_in_json([payload])) == 1


def test_json_item_with_falsy_stock_flag_is_ignored():
    payload = {"versions": [{"title": "V2 RWD Long Range", "inStock": False}]}
    assert find_stock_in_json([payload]) == []


def test_json_non_dict_payloads_do_not_crash():
    assert find_stock_in_json(["abc", 42, None, [1, 2]]) == []


# ---------------------------------------------------------------------------
# Durum farkı: sadece yeni görülen araçlar bildirilir
# ---------------------------------------------------------------------------


def test_new_matches_returns_only_unseen_items():
    old = ["T10F V2 RWD Uzun Menzil | Urla Yeşil"]
    new = ["T10F V2 RWD Uzun Menzil | Urla Yeşil", "T10F V2 RWD Uzun Menzil | Beyaz"]
    assert new_matches(old, new) == ["T10F V2 RWD Uzun Menzil | Beyaz"]


def test_new_matches_empty_when_stock_disappears():
    assert new_matches(["x"], []) == []


# ---------------------------------------------------------------------------
# .env okuma
# ---------------------------------------------------------------------------


def test_parse_env_file_handles_comments_quotes_and_blank_lines():
    content = (
        "# yorum\n"
        "\n"
        "TELEGRAM_BOT_TOKEN=123:abc\n"
        'TELEGRAM_CHAT_ID="42"\n'
        "CHECK_INTERVAL = 300\n"
    )
    assert parse_env_file(content) == {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_CHAT_ID": "42",
        "CHECK_INTERVAL": "300",
    }


# ---------------------------------------------------------------------------
# --max-runtime: döngü süresi dolunca çıkar (GitHub Actions için)
# ---------------------------------------------------------------------------


def test_run_stops_after_max_runtime(monkeypatch, tmp_path):
    import togg_stok_bot as bot

    calls = []
    clock = {"t": 0.0}
    monkeypatch.setattr(bot, "check_once", lambda url, state, dump_dir=None: calls.append(1))
    monkeypatch.setattr(bot.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(bot.time, "monotonic", lambda: clock["t"])

    rc = bot.run("http://x", interval=240, once=False, state_path=tmp_path / "s.json",
                 dump_dir=None, max_runtime=1000)

    assert rc == 0
    # t=0, 240, 480, 720, 960 kontrol; 960+240 > 1000 olduğundan 6. kontrol yapılmaz
    assert len(calls) == 5
