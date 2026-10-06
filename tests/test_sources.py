"""Доски по ссылке, другие сайты, способы входа, текст в картинке, коллекция без anime-paths.json."""
from __future__ import annotations

import json

import pytest

from anime_vault import browsers, download, manifest
from anime_vault.known import pin_key


@pytest.mark.parametrize(("address", "expected"), [
    ("https://www.pinterest.com/someone/01-anime/", {"slug": "01-anime"}),
    ("https://ru.pinterest.com/someone/02-%D1%88%D0%B5%D0%B4%D0%B5%D0%B2%D1%80%D1%8B/", {"slug": "02-%D1%88%D0%B5%D0%B4%D0%B5%D0%B2%D1%80%D1%8B"}),
    ("https://www.pinterest.com/someone/02-шедевры", {"slug": "02-%D1%88%D0%B5%D0%B4%D0%B5%D0%B2%D1%80%D1%8B"}),
    ("https://pinterest.co.uk/Other_User/cats/", {"slug": "cats", "user": "Other_User"}),
    ("https://danbooru.donmai.us/posts?tags=frieren", {"url": "https://danbooru.donmai.us/posts?tags=frieren"}),
    ("01-anime", {"slug": "01-anime"}),
])
def test_parse_address(address, expected):
    assert manifest.parse_address(address, "someone") == expected


@pytest.mark.parametrize("address", ["", "https://www.pinterest.com/pin/123/", "https://pin.it/abc", "two words"])
def test_parse_address_errors(address):
    with pytest.raises(ValueError):
        manifest.parse_address(address, "someone")


def test_names_and_users_from_links():
    assert manifest.name_from_address("https://www.pinterest.com/someone/01-%D0%B0n-i-m%D0%B5/") == "01 аn i mе"
    assert manifest.name_from_address("https://danbooru.donmai.us/posts?tags=frieren+rating:g") == "frieren rating g"
    assert manifest.name_from_address("https://x.com/artist/media") == "artist media"
    assert manifest.pinterest_user("https://www.pinterest.com/someone/cats/") == "someone"
    assert manifest.pinterest_user("https://danbooru.donmai.us/posts") == ""


def test_board_address():
    assert manifest.Board("a", slug="cats").address("me") == "https://www.pinterest.com/me/cats/"
    assert manifest.Board("a", slug="cats", user="you").address("me") == "https://www.pinterest.com/you/cats/"
    assert manifest.Board("a", url="https://x.com/artist/media").address("me") == "https://x.com/artist/media"


def test_site_keys():
    assert pin_key("danbooru_123.jpg") == "danbooru_123"
    assert pin_key("Twitter_456_2.png") == "twitter_456_2"
    assert pin_key("Screenshot_2024.png") is None


def test_cookies():
    opera = browsers.parse("opera:C:/Users/me/AppData/Roaming/Opera Software/Opera GX Stable")
    assert (opera.kind, opera.browser, opera.name, opera.must_close) == ("browser", "opera", "Opera GX", True)
    assert opera.arguments() == ["--cookies-from-browser", "opera:C:/Users/me/AppData/Roaming/Opera Software/Opera GX Stable"]
    firefox = browsers.parse("firefox")
    assert (firefox.name, firefox.must_close, firefox.arguments()) == ("Firefox", False, ["--cookies-from-browser", "firefox"])
    file = browsers.parse("C:/cookies.txt")
    assert (file.kind, file.arguments(), file.spec()) == ("file", ["--cookies", "C:/cookies.txt"], "C:/cookies.txt")
    assert browsers.parse("").arguments() == []


def test_pin_text_from_other_sites():
    title, description = download.pin_text({"tag_string_character": "frieren", "tag_string_copyright": "sousou_no_frieren"})
    assert title == "" and description == "Characters: frieren\nCopyright: sousou_no_frieren"
    assert download.pin_text({"title": "T", "content": "tweet"}) == ("T", "tweet")


def test_gallery_config(tmp_path):
    download.gallery_config(tmp_path / "c.json", tmp_path / "a.sqlite3")
    config = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["extractor"]
    assert config["filename"] == "{category}_{id}{num:?_//}.{extension}"
    assert config["pinterest"]["archive-format"] == "{id}{media_id|page_id:?_//}"


def test_collection_without_manifest(tmp_path, monkeypatch):
    """anime-vault сам по себе: нет anime-paths.json — пачки в dataN, источники — доски, кроме «только хранить»."""
    settings = tmp_path / "config.json"
    settings.write_text(json.dumps({"distribute": False, "pinterest": {
        "browser_cookies": "firefox",
        "boards": [{"name": "Cats", "url": "https://danbooru.donmai.us/posts?tags=cat"}, {"name": "Best", "slug": "best", "keep": True}],
    }}), encoding="utf-8")
    monkeypatch.setattr(manifest, "SETTINGS", settings)
    root = tmp_path / "Pictures"
    root.mkdir()
    loaded = manifest.load(root)
    assert loaded.batches == root / "dataN"
    assert loaded.sources == [root / "downloads" / "Cats"]
    assert loaded.distribute is False
    assert loaded.boards[0].url.startswith("https://danbooru")
