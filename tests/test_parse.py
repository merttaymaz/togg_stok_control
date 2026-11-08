from togg_stok_bot import (
    find_target_products,
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
    monkeypatch.setattr(bot, "check_once", lambda url, state, dump_dir=None, **kw: calls.append(1))
    monkeypatch.setattr(bot.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(bot.time, "monotonic", lambda: clock["t"])

    rc = bot.run("http://x", interval=240, once=False, state_path=tmp_path / "s.json",
                 dump_dir=None, max_runtime=1000)

    assert rc == 0
    # t=0, 240, 480, 720, 960 kontrol; 960+240 > 1000 olduğundan 6. kontrol yapılmaz
    assert len(calls) == 5


# ---------------------------------------------------------------------------
# API modu: getSmartDeviceProducts yanıtında hedef ürün var mı?
# ---------------------------------------------------------------------------

PRODUCTS_4MORE_ONLY = {
    "products": [
        {
            "id": "prod_1",
            "name": "T10F V2 4More",
            "type_name": "T10F V2 4More",
            "model": {"name": "T10F", "year": 2026},
            "price": {"sct_65": {"with_all_taxes": 3167000.001}},
        },
        {
            "id": "prod_2",
            "name": "T10F V2 4More Kehribar",
            "type_name": "T10F V2 4More",
            "model": {"name": "T10F", "year": 2026},
            "price": {"sct_65": {"with_all_taxes": 3167000.001}},
        },
    ]
}

PRODUCTS_WITH_RWD = {
    "products": PRODUCTS_4MORE_ONLY["products"]
    + [
        {
            "id": "prod_3",
            "name": "T10F V2 RWD Uzun Menzil",
            "type_name": "T10F V2 RWD Uzun Menzil",
            "model": {"name": "T10F", "year": 2026},
            "price": {
                "sct_25": {"with_all_taxes": 2393000.0},
                "sct_55": {"with_all_taxes": 2907800.0},
            },
        }
    ]
}


def test_products_with_only_4more_give_no_match():
    assert find_target_products(PRODUCTS_4MORE_ONLY) == []


def test_v2_rwd_product_is_matched_with_year_and_price():
    matches = find_target_products(PRODUCTS_WITH_RWD)
    assert len(matches) == 1
    assert matches[0].startswith("T10F V2 RWD Uzun Menzil")
    assert "2026" in matches[0]
    assert "2.907.800" in matches[0]


def test_v2_long_range_without_rwd_in_name_still_matches():
    payload = {"products": [{"name": "T10F V2 Uzun Menzil", "price": {}}]}
    assert len(find_target_products(payload)) == 1


def test_products_payload_without_products_key_is_empty():
    assert find_target_products({"error": "x"}) == []
    assert find_target_products(None) == []


# ---------------------------------------------------------------------------
# Teşhis: getUpdates yanıtından bota yazan sohbetleri çıkar
# ---------------------------------------------------------------------------


def test_chats_from_updates_lists_each_chat_once():
    from togg_stok_bot import chats_from_updates

    updates = {
        "ok": True,
        "result": [
            {"update_id": 1, "message": {"chat": {"id": 42, "first_name": "Mert", "type": "private"}}},
            {"update_id": 2, "message": {"chat": {"id": 42, "first_name": "Mert", "type": "private"}}},
            {"update_id": 3, "my_chat_member": {"chat": {"id": -100, "title": "Grup", "type": "group"}}},
        ],
    }
    assert chats_from_updates(updates) == [(42, "Mert"), (-100, "Grup")]


def test_chats_from_updates_handles_empty():
    from togg_stok_bot import chats_from_updates

    assert chats_from_updates({"ok": True, "result": []}) == []


# ---------------------------------------------------------------------------
# Telegram gönderimi: HTML reddedilirse düz metinle tekrar dene
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_send_telegram_falls_back_to_plain_text_when_html_rejected(monkeypatch):
    import togg_stok_bot as bot

    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        if "parse_mode" in json:
            return _Resp(400, {"ok": False, "description": "Bad Request: can't parse entities"})
        return _Resp(200, {"ok": True})

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(bot.requests, "post", fake_post)

    bot.send_telegram("<b>x</b>")

    assert len(calls) == 2
    assert "parse_mode" not in calls[1]
    assert "<b>" not in calls[1]["text"]  # etiketler düz metinde temizlenir


def test_send_telegram_raises_with_description_when_both_fail(monkeypatch):
    import pytest
    import togg_stok_bot as bot

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(
        bot.requests, "post",
        lambda url, json, timeout: _Resp(400, {"ok": False, "description": "chat not found"}),
    )
    with pytest.raises(RuntimeError, match="chat not found"):
        bot.send_telegram("x")
