"""
Fashion Press コレクション一覧スクレイピングツール

対象:
    https://www.fashion-press.net/collections/

仕様:
    - GUIでシーズンを複数選択
    - GUIで場所を選択
    - 各シーズンの選択場所コレクション一覧1ページ目に表示されているブランドだけ取得
    - ブランド詳細ページから公式サイトURLとブランド概要を取得
    - CSVに保存
    - 次ページ巡回はしない
    - SQLite履歴DBで取得済みブランドはスキップ

実行:
    python3 fashion_press_collections_scraper.py
"""

import csv
import json
import logging
import re
import smtplib
import sqlite3
import ssl
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk


BASE_URL = "https://www.fashion-press.net"
COLLECTIONS_URL = f"{BASE_URL}/collections/"
LOCATION_OPTIONS = [
    ("東京", "tokyo"),
    ("その他", "other"),
]

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
LOG_FILE = SCRIPT_DIR / "fashion_press_collections.log"
DB_PATH = SCRIPT_DIR / "fashion_press_history.db"
EMAIL_SETTINGS_PATH = SCRIPT_DIR / "email_settings.json"

try:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    OUTPUT_DIR = Path(tempfile.gettempdir()) / "fashion_press_collections_output"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REQUEST_TIMEOUT = 30
WAIT_BETWEEN_LIST_PAGES = 5.0
WAIT_BETWEEN_BRAND_PAGES = 6.0
MAX_RETRY = 3
ALLOW_UNVERIFIED_SSL_FALLBACK = True

DEFAULT_SEASONS = [
    ("2027年春夏", "2027ss"),
    ("2026-27年秋冬", "2026-27aw"),
    ("2026年春夏", "2026ss"),
    ("2025-26年秋冬", "2025-26aw"),
    ("2025年春夏", "2025ss"),
    ("2024-25年秋冬", "2024-25aw"),
    ("2024年春夏", "2024ss"),
    ("2023-24年秋冬", "2023-24aw"),
    ("2023年春夏", "2023ss"),
    ("2022-23年秋冬", "2022-23aw"),
    ("2022年春夏", "2022ss"),
    ("2021-22年秋冬", "2021-22aw"),
    ("2021年春夏", "2021ss"),
    ("2020-21年秋冬", "2020-21aw"),
    ("2020年春夏", "2020ss"),
    ("2019-20年秋冬", "2019-20aw"),
    ("2019年春夏", "2019ss"),
    ("2018-19年秋冬", "2018-19aw"),
    ("2018年春夏", "2018ss"),
    ("2017-18年秋冬", "2017-18aw"),
    ("2017年春夏", "2017ss"),
    ("2016-17年秋冬", "2016-17aw"),
    ("2016年春夏", "2016ss"),
    ("2015-16年秋冬", "2015-16aw"),
    ("2015年春夏", "2015ss"),
    ("2014-15年秋冬", "2014-15aw"),
    ("2014年春夏", "2014ss"),
    ("2013-14年秋冬", "2013-14aw"),
    ("2013年春夏", "2013ss"),
    ("2012-13年秋冬", "2012-13aw"),
    ("2012年春夏", "2012ss"),
    ("2011-12年秋冬", "2011-12aw"),
    ("2011年春夏", "2011ss"),
    ("2010-11年秋冬", "2010-11aw"),
    ("2010年春夏", "2010ss"),
    ("2009-10年秋冬", "2009-10aw"),
]

OUTPUT_COLUMNS = [
    "ブランド",
    "ブランド（カタカナ）",
    "ブランドURL",
    "ブランド概要",
]

MANUAL_HISTORY_CSV = OUTPUT_DIR / "history_manual.csv"
MANUAL_HISTORY_COLUMNS = [
    "brand_page_url",
    "ブランド",
    "ブランド（カタカナ）",
    "ブランドURL",
    "ブランド概要",
]

DEFAULT_EMAIL_SETTINGS = {
    "enabled": False,
    "to_email": "",
    "from_email": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": "587",
    "smtp_user": "",
    "use_tls": True,
}


logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)


def log(message: str):
    print(message)
    logging.info(message)


def load_email_settings() -> dict:
    settings = DEFAULT_EMAIL_SETTINGS.copy()

    if not EMAIL_SETTINGS_PATH.exists():
        return settings

    try:
        with EMAIL_SETTINGS_PATH.open("r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            settings.update({key: loaded.get(key, value) for key, value in settings.items()})

    except Exception as e:
        log(f"メール設定の読み込みをスキップしました: {e}")

    return settings


def save_email_settings(settings: dict):
    safe_settings = {
        key: settings.get(key, DEFAULT_EMAIL_SETTINGS[key])
        for key in DEFAULT_EMAIL_SETTINGS
    }

    with EMAIL_SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(safe_settings, f, ensure_ascii=False, indent=2)


def build_completion_email_body(result: dict) -> str:
    lines = [
        "Fashion Press ブランド概要取得が完了しました。",
        "",
        f"停止しました: {'はい' if result.get('停止しました') else 'いいえ'}",
        f"一覧取得ページ数: {result.get('一覧取得ページ数', 0)}",
        f"ブランド詳細取得ページ数: {result.get('ブランド詳細取得ページ数', 0)}",
        f"手動履歴CSV取り込み件数: {result.get('手動履歴CSV取り込み件数', 0)}",
        f"履歴スキップ件数: {result.get('履歴スキップ件数', 0)}",
        f"CSV出力件数: {result.get('CSV出力件数', 0)}",
        f"CSV保存: {'あり' if result.get('CSV保存済み') else 'なし'}",
        f"CSV保存先: {result.get('CSV保存先', '')}",
    ]
    return "\n".join(lines)


def send_completion_email(settings: dict, password: str, result: dict):
    message = EmailMessage()
    message["Subject"] = "Fashion Press ブランド概要取得 完了通知"
    message["From"] = settings["from_email"]
    message["To"] = settings["to_email"]
    message.set_content(build_completion_email_body(result))

    smtp_port = int(settings["smtp_port"])

    with smtplib.SMTP(settings["smtp_host"], smtp_port, timeout=30) as smtp:
        if settings.get("use_tls", True):
            smtp.starttls()

        smtp.login(settings["smtp_user"], password)
        smtp.send_message(message)


def clean_text(text) -> str:
    text = "" if text is None else str(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[\r\n\t]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def timestamp_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_season_location_url(season_slug: str, location_slug: str, page_number: int = 1) -> str:
    base = f"{BASE_URL}/collections/search/{season_slug}/{location_slug}"

    if page_number <= 1:
        return base

    return f"{base}?page={page_number}"


def canonical_text(value: str) -> str:
    return clean_text(value)


def canonical_url(value: str) -> str:
    value = clean_text(value)

    if value.endswith("/"):
        return value.rstrip("/")

    return value


def create_manual_history_template():
    if MANUAL_HISTORY_CSV.exists():
        return

    with MANUAL_HISTORY_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MANUAL_HISTORY_COLUMNS)
        writer.writeheader()


def build_verified_ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def is_ssl_certificate_error(error: Exception) -> bool:
    reason = getattr(error, "reason", None)

    if isinstance(reason, ssl.SSLCertVerificationError):
        return True

    return "CERTIFICATE_VERIFY_FAILED" in str(error)


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    last_error = ""
    ssl_contexts = [("SSL検証あり", build_verified_ssl_context())]

    if ALLOW_UNVERIFIED_SSL_FALLBACK:
        ssl_contexts.append(("SSL検証なし", ssl._create_unverified_context()))

    for context_label, ssl_context in ssl_contexts:
        for attempt in range(1, MAX_RETRY + 1):
            try:
                request = Request(url, headers=headers)

                with urlopen(request, timeout=REQUEST_TIMEOUT, context=ssl_context) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read().decode(charset, errors="replace")

            except (HTTPError, URLError, TimeoutError, ssl.SSLError) as e:
                last_error = str(e)

                if context_label == "SSL検証あり" and is_ssl_certificate_error(e):
                    log(
                        "SSL証明書の検証に失敗しました。"
                        "ローカル環境の証明書ストアが原因の可能性があります。"
                    )
                    break

                log(f"取得失敗: {url} / {context_label} / 試行{attempt}/{MAX_RETRY} / {e}")
                time.sleep(WAIT_BETWEEN_BRAND_PAGES * attempt)

        if context_label == "SSL検証あり" and ALLOW_UNVERIFIED_SSL_FALLBACK:
            log("SSL検証なしのフォールバックで再取得します。")

    raise RuntimeError(f"ページを取得できませんでした: {url} / {last_error}")


def attrs_to_dict(attrs) -> dict:
    return {key: value or "" for key, value in attrs}


def has_class(attrs: dict, class_name: str) -> bool:
    return class_name in attrs.get("class", "").split()


class SeasonParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.seasons = []
        self.current_href = ""
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        attrs = attrs_to_dict(attrs)

        if tag == "a":
            href = attrs.get("href", "")

            if re.fullmatch(r"/collections/search/[0-9]{4}(?:-[0-9]{2})?(?:ss|aw)", href):
                self.current_href = href
                self.current_text = []

    def handle_data(self, data):
        if self.current_href:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or not self.current_href:
            return

        text = clean_text("".join(self.current_text))
        slug = self.current_href.rsplit("/", 1)[-1]

        if text and (text, slug) not in self.seasons:
            self.seasons.append((text, slug))

        self.current_href = ""
        self.current_text = []


class CollectionListParser(HTMLParser):
    def __init__(self, source_url: str, season_label: str, location_label: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.season_label = season_label
        self.location_label = location_label
        self.items = []
        self.has_next_page = False

        self.in_article = False
        self.article_depth = 0
        self.in_h3 = False
        self.h3_depth = 0
        self.in_brand = False
        self.current = {}
        self.h3_text_parts = []
        self.brand_text_parts = []

        self.current_anchor_href = ""
        self.current_anchor_text = []

    def handle_starttag(self, tag, attrs):
        attrs = attrs_to_dict(attrs)

        if tag == "article" and has_class(attrs, "fp_list_each"):
            self.in_article = True
            self.article_depth = 1
            self.current = {
                "シーズン": self.season_label,
                "場所": self.location_label,
                "ブランド": "",
                "ブランドURL": "",
                "コレクション名": "",
                "性別": "",
                "コレクションURL": "",
                "画像URL": "",
                "一覧URL": self.source_url,
            }
            self.h3_text_parts = []
            self.brand_text_parts = []
            return

        if self.in_article:
            self.article_depth += 1

            if tag == "h3":
                self.in_h3 = True
                self.h3_depth = 1
                return

            if self.in_h3:
                self.h3_depth += 1

            if tag == "a":
                href = attrs.get("href", "")
                absolute_url = urljoin(BASE_URL, href)

                if re.fullmatch(rf"{re.escape(BASE_URL)}/collections/\d+", absolute_url):
                    self.current["コレクションURL"] = self.current["コレクションURL"] or absolute_url
                    title = clean_text(attrs.get("title", ""))

                    if title:
                        self.current["コレクション名"] = self.current["コレクション名"] or title

                if has_class(attrs, "brand_a") or "/collections/brand/" in href:
                    self.in_brand = True
                    self.current["ブランドURL"] = absolute_url

            if tag == "img":
                src = attrs.get("src") or attrs.get("data-src")

                if src:
                    self.current["画像URL"] = urljoin(BASE_URL, src)

        elif tag == "a":
            href = attrs.get("href", "")
            self.current_anchor_href = href
            self.current_anchor_text = []

    def handle_data(self, data):
        if self.in_article:
            if self.in_h3:
                self.h3_text_parts.append(data)

            if self.in_brand:
                self.brand_text_parts.append(data)
        elif self.current_anchor_href:
            self.current_anchor_text.append(data)

    def handle_endtag(self, tag):
        if self.in_article:
            if tag == "a" and self.in_brand:
                self.in_brand = False

            if self.in_h3:
                self.h3_depth -= 1

                if self.h3_depth <= 0:
                    self.in_h3 = False

            self.article_depth -= 1

            if tag == "article" or self.article_depth <= 0:
                self.finish_article()
                self.in_article = False
                self.article_depth = 0

        elif tag == "a" and self.current_anchor_href:
            text = clean_text("".join(self.current_anchor_text))
            href = self.current_anchor_href

            if text in {"次", "次へ"} and re.search(r"[?&]page=\d+", href):
                self.has_next_page = True

            self.current_anchor_href = ""
            self.current_anchor_text = []

    def finish_article(self):
        h3_text = clean_text(" ".join(self.h3_text_parts))
        brand_text = clean_text(" ".join(self.brand_text_parts))

        if h3_text:
            self.current["コレクション名"] = h3_text

        if brand_text:
            self.current["ブランド"] = brand_text

        if not self.current["ブランド"]:
            self.current["ブランド"] = infer_brand_from_title(self.current["コレクション名"])

        self.current["性別"] = infer_gender(self.current["コレクション名"])

        if self.current["コレクションURL"]:
            self.items.append(self.current)


class BrandDetailParser(HTMLParser):
    def __init__(self, fallback_kana_name: str, official_brand_page_url: str):
        super().__init__(convert_charrefs=True)
        self.fallback_kana_name = fallback_kana_name
        self.official_brand_page_url = official_brand_page_url
        self.brand_name = ""
        self.brand_kana_name = fallback_kana_name
        self.official_url = ""
        self.description_parts = []

        self.in_main_content = False
        self.in_h1 = False
        self.in_h2 = False
        self.in_h3 = False
        self.current_heading_tag = ""
        self.current_heading_text = []
        self.current_h2_text = ""
        self.capture_description = False
        self.capture_official_url = False

    def handle_starttag(self, tag, attrs):
        attrs = attrs_to_dict(attrs)

        if attrs.get("id") == "main-content":
            self.in_main_content = True

        if not self.in_main_content:
            return

        if tag == "h1":
            self.in_h1 = True
            return

        if tag in {"h2", "h3"}:
            self.in_h2 = tag == "h2"
            self.in_h3 = tag == "h3"
            self.current_heading_tag = tag
            self.current_heading_text = []
            return

        if tag == "a" and self.capture_official_url and not self.official_url:
            href = attrs.get("href", "")

            if href.startswith(("http://", "https://")):
                self.official_url = href

    def handle_data(self, data):
        if not self.in_main_content:
            return

        if self.in_h1:
            text = clean_text(data)

            if text:
                self.brand_kana_name = text
            return

        if self.in_h2 or self.in_h3:
            self.current_heading_text.append(data)
            return

        if self.capture_description:
            text = clean_text(data)

            if text:
                self.description_parts.append(text)

    def handle_endtag(self, tag):
        if not self.in_main_content:
            return

        if tag == "h1":
            self.in_h1 = False
            return

        if tag in {"h2", "h3"} and tag == self.current_heading_tag:
            heading = clean_text("".join(self.current_heading_text))

            if tag == "h2":
                self.current_h2_text = heading

                if heading and heading not in {"公式サイト"} and not self.brand_name:
                    self.brand_name = heading

                if heading == "公式サイト":
                    self.capture_description = False
                    self.capture_official_url = True
                elif heading.startswith("Top Stories") or heading.startswith("Collections") or heading.startswith("News") or heading.startswith("Shop"):
                    self.capture_description = False
                    self.capture_official_url = False

            if tag == "h3":
                if "について" in heading or "ブランドのはじまり" in heading:
                    self.capture_description = True
                    self.capture_official_url = False

                    if heading:
                        self.description_parts.append(heading)

            self.in_h2 = False
            self.in_h3 = False
            self.current_heading_tag = ""
            self.current_heading_text = []

    def result(self) -> dict:
        return {
            "ブランド": clean_text(self.brand_name),
            "ブランド（カタカナ）": clean_text(self.brand_kana_name),
            "ブランドURL": clean_text(self.official_url),
            "ブランド概要": clean_text(" ".join(self.description_parts)),
        }


def infer_brand_from_title(title: str) -> str:
    title = clean_text(title)
    markers = [
        r"\s+\d{4}(?:-\d{2})?年",
        r"\s+\d{4}(?:-\d{2})?SS",
        r"\s+\d{4}(?:-\d{2})?AW",
        r"\s+\d{4}年",
    ]

    for marker in markers:
        match = re.search(marker, title, flags=re.IGNORECASE)

        if match:
            return clean_text(title[:match.start()])

    return ""


def infer_gender(title: str) -> str:
    title = clean_text(title)

    for keyword in ["ウィメンズ&メンズ", "ウィメンズ＆メンズ", "ウィメンズ", "メンズ"]:
        if keyword in title:
            return keyword

    return ""


def fetch_seasons_from_site() -> list[tuple[str, str]]:
    html = fetch_html(COLLECTIONS_URL)
    parser = SeasonParser()
    parser.feed(html)
    return parser.seasons or DEFAULT_SEASONS


def parse_collections(
    html: str,
    source_url: str,
    season_label: str,
    location_label: str,
) -> tuple[list[dict], bool]:
    parser = CollectionListParser(source_url, season_label, location_label)
    parser.feed(html)
    return parser.items, parser.has_next_page


def collection_brand_url_to_brand_page_url(collection_brand_url: str) -> str:
    match = re.search(r"/collections/brand/(\d+)", collection_brand_url)

    if not match:
        return collection_brand_url

    return f"{BASE_URL}/brands/{match.group(1)}"


def parse_brand_detail(html: str, fallback_kana_name: str, brand_page_url: str) -> dict:
    parser = BrandDetailParser(fallback_kana_name, brand_page_url)
    parser.feed(html)
    result = parser.result()

    if not result["ブランド"]:
        result["ブランド"] = infer_latin_brand_name_from_title(html)

    if not result["ブランド（カタカナ）"]:
        result["ブランド（カタカナ）"] = fallback_kana_name

    return result


def infer_latin_brand_name_from_title(html: str) -> str:
    match = re.search(r"<title>\s*([^:<｜|]+)", html, flags=re.IGNORECASE)

    if not match:
        return ""

    title_head = clean_text(match.group(1))

    if " : " in title_head:
        return clean_text(title_head.split(" : ", 1)[1])

    return ""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS acquired_brands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_page_url TEXT,
        brand TEXT,
        brand_kana TEXT,
        official_url TEXT,
        overview TEXT,
        first_acquired_at TEXT,
        last_acquired_at TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acquired_brand_page_url ON acquired_brands(brand_page_url)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acquired_brand ON acquired_brands(brand)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acquired_brand_kana ON acquired_brands(brand_kana)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acquired_official_url ON acquired_brands(official_url)")
    conn.commit()
    conn.close()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_acquired_brand(
    brand_page_url: str = "",
    brand: str = "",
    brand_kana: str = "",
    official_url: str = "",
) -> bool:
    checks = [
        ("brand_page_url", canonical_url(brand_page_url)),
        ("brand", canonical_text(brand)),
        ("brand_kana", canonical_text(brand_kana)),
        ("official_url", canonical_url(official_url)),
    ]
    checks = [(column, value) for column, value in checks if value]

    if not checks:
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for column, value in checks:
        cur.execute(f"SELECT 1 FROM acquired_brands WHERE {column} = ? LIMIT 1", (value,))

        if cur.fetchone():
            conn.close()
            return True

    conn.close()
    return False


def save_acquired_brand(row: dict, brand_page_url: str):
    now = now_str()
    values = {
        "brand_page_url": canonical_url(brand_page_url),
        "brand": canonical_text(row.get("ブランド", "")),
        "brand_kana": canonical_text(row.get("ブランド（カタカナ）", "")),
        "official_url": canonical_url(row.get("ブランドURL", "")),
        "overview": clean_text(row.get("ブランド概要", "")),
    }

    if is_acquired_brand(
        values["brand_page_url"],
        values["brand"],
        values["brand_kana"],
        values["official_url"],
    ):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        update_conditions = []
        params = []

        for column in ["brand_page_url", "brand", "brand_kana", "official_url"]:
            if values[column]:
                update_conditions.append(f"{column} = ?")
                params.append(values[column])

        if update_conditions:
            cur.execute(
                f"UPDATE acquired_brands SET last_acquired_at = ? WHERE {' OR '.join(update_conditions)}",
                [now] + params,
            )
            conn.commit()
        conn.close()
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO acquired_brands (
            brand_page_url,
            brand,
            brand_kana,
            official_url,
            overview,
            first_acquired_at,
            last_acquired_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            values["brand_page_url"],
            values["brand"],
            values["brand_kana"],
            values["official_url"],
            values["overview"],
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def import_previous_csvs_to_db() -> int:
    init_db()
    imported = 0
    candidate_dirs = [
        OUTPUT_DIR,
        SCRIPT_DIR / "outputs" / "output",
    ]

    for candidate_dir in candidate_dirs:
        if not candidate_dir.exists():
            continue

        for csv_file in candidate_dir.glob("fashion_press*_brand_overviews_*.csv"):
            try:
                with csv_file.open("r", newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)

                    for row in reader:
                        brand = canonical_text(row.get("ブランド", ""))
                        brand_kana = canonical_text(row.get("ブランド（カタカナ）", ""))
                        official_url = canonical_url(row.get("ブランドURL", ""))

                        if not any([brand, brand_kana, official_url]):
                            continue

                        if is_acquired_brand(brand=brand, brand_kana=brand_kana, official_url=official_url):
                            continue

                        save_acquired_brand(row, "")
                        imported += 1

            except Exception as e:
                log(f"過去CSVの履歴取り込みをスキップしました: {csv_file} / {e}")

    return imported


def get_row_value(row: dict, keys: list[str]) -> str:
    for key in keys:
        value = clean_text(row.get(key, ""))

        if value:
            return value

    return ""


def import_manual_history_csv_to_db() -> int:
    init_db()
    create_manual_history_template()

    if not MANUAL_HISTORY_CSV.exists():
        return 0

    imported = 0

    try:
        with MANUAL_HISTORY_CSV.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            for row in reader:
                brand_page_url = canonical_url(
                    get_row_value(row, ["brand_page_url", "ブランド詳細URL", "Fashion PressブランドURL"])
                )
                brand = canonical_text(get_row_value(row, ["ブランド", "brand"]))
                brand_kana = canonical_text(get_row_value(row, ["ブランド（カタカナ）", "brand_kana"]))
                official_url = canonical_url(get_row_value(row, ["ブランドURL", "official_url"]))

                if not any([brand_page_url, brand, brand_kana, official_url]):
                    continue

                if is_acquired_brand(
                    brand_page_url=brand_page_url,
                    brand=brand,
                    brand_kana=brand_kana,
                    official_url=official_url,
                ):
                    continue

                save_acquired_brand(
                    {
                        "ブランド": brand,
                        "ブランド（カタカナ）": brand_kana,
                        "ブランドURL": official_url,
                        "ブランド概要": clean_text(row.get("ブランド概要", "")),
                    },
                    brand_page_url,
                )
                imported += 1

    except Exception as e:
        log(f"手動履歴CSVの取り込みをスキップしました: {MANUAL_HISTORY_CSV} / {e}")

    return imported


def save_csv(rows: list[dict], output_csv: Path):
    with output_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


@dataclass
class ScrapeConfig:
    seasons: list[tuple[str, str]]
    locations: list[tuple[str, str]]
    save_empty_csv: bool


def run_scraping(config: ScrapeConfig, stop_event=None) -> dict:
    init_db()
    imported_history_count = import_previous_csvs_to_db()
    imported_manual_history_count = import_manual_history_csv_to_db()
    rows = []
    brand_candidates = {}
    output_csv = OUTPUT_DIR / f"fashion_press_tokyo_other_brand_overviews_{timestamp_str()}.csv"
    stopped = False
    fetched_pages = 0
    fetched_brand_pages = 0
    skipped_by_history_count = 0
    duplicate_candidate_count = 0

    log("========================================")
    location_names = "・".join(label for label, _slug in config.locations)
    log("Fashion Press コレクション ブランド概要取得開始")
    log(f"選択シーズン数: {len(config.seasons)}")
    log(f"選択場所: {location_names}")
    log("取得範囲: 各シーズンの選択場所一覧1ページ目に表示されているブランドのみ")
    log(f"過去CSVから履歴DBへ取り込んだ件数: {imported_history_count}")
    log(f"手動履歴CSVから履歴DBへ取り込んだ件数: {imported_manual_history_count}")
    log("========================================")

    for season_label, season_slug in config.seasons:
        for location_label, location_slug in config.locations:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                log("停止要求を受け付けたため処理を終了します。")
                break

            url = build_season_location_url(season_slug, location_slug, 1)
            log(f"一覧取得: {season_label} / {location_label} / 1ページ目のみ / {url}")
            html = fetch_html(url)
            fetched_pages += 1

            items, _has_next_page = parse_collections(html, url, season_label, location_label)
            log(f"このページに表示されているブランド候補: {len(items)}件")

            for item in items:
                collection_brand_url = item.get("ブランドURL", "")

                if not collection_brand_url:
                    continue

                brand_page_url = collection_brand_url_to_brand_page_url(collection_brand_url)
                fallback_kana_name = item.get("ブランド", "")

                if is_acquired_brand(brand_page_url=brand_page_url, brand_kana=fallback_kana_name):
                    skipped_by_history_count += 1
                    log(f"履歴スキップ: {fallback_kana_name} / {brand_page_url}")
                    continue

                if brand_page_url in brand_candidates:
                    duplicate_candidate_count += 1
                    continue

                brand_candidates[brand_page_url] = fallback_kana_name

            time.sleep(WAIT_BETWEEN_LIST_PAGES)

        if stopped:
            break

    if not stopped:
        log(f"ブランド詳細取得対象: {len(brand_candidates)}件")

        for brand_page_url, fallback_kana_name in brand_candidates.items():
            if stop_event is not None and stop_event.is_set():
                stopped = True
                log("停止要求を受け付けたためブランド詳細取得を終了します。")
                break

            log(f"ブランド詳細取得: {brand_page_url}")
            html = fetch_html(brand_page_url)
            fetched_brand_pages += 1
            brand_row = parse_brand_detail(html, fallback_kana_name, brand_page_url)

            if is_acquired_brand(
                brand_page_url=brand_page_url,
                brand=brand_row.get("ブランド", ""),
                brand_kana=brand_row.get("ブランド（カタカナ）", ""),
                official_url=brand_row.get("ブランドURL", ""),
            ):
                skipped_by_history_count += 1
                log(
                    "履歴スキップ: "
                    f"{brand_row.get('ブランド', '')} / "
                    f"{brand_row.get('ブランド（カタカナ）', '')} / "
                    f"{brand_page_url}"
                )
                time.sleep(WAIT_BETWEEN_BRAND_PAGES)
                continue

            if brand_row["ブランド（カタカナ）"] or brand_row["ブランド"]:
                rows.append(brand_row)
                save_acquired_brand(brand_row, brand_page_url)

            time.sleep(WAIT_BETWEEN_BRAND_PAGES)

    if rows or config.save_empty_csv:
        save_csv(rows, output_csv)
        csv_saved = True
        csv_path = str(output_csv.resolve())
    else:
        csv_saved = False
        csv_path = ""

    result = {
        "停止しました": stopped,
        "一覧取得ページ数": fetched_pages,
        "ブランド詳細取得ページ数": fetched_brand_pages,
        "履歴スキップ件数": skipped_by_history_count,
        "手動履歴CSV取り込み件数": imported_manual_history_count,
        "一覧内重複スキップ件数": duplicate_candidate_count,
        "CSV出力件数": len(rows),
        "CSV保存済み": csv_saved,
        "CSV保存先": csv_path,
    }

    log("========================================")
    log("処理完了")
    for key, value in result.items():
        log(f"{key}: {value}")
    log("========================================")
    return result


class FashionPressCollectionsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Fashion Press 東京・その他コレクション取得ツール")
        self.root.geometry("880x820")
        self.root.minsize(820, 760)
        self.root.resizable(True, True)

        self.seasons = DEFAULT_SEASONS[:]
        self.location_vars = {}
        self.email_password_var = tk.StringVar(value="")
        self.is_running = False
        self.stop_event = None

        self.create_widgets()

    def create_widgets(self):
        title_label = tk.Label(
            self.root,
            text="Fashion Press 東京・その他コレクション取得ツール",
            font=("Meiryo", 15, "bold"),
        )
        title_label.pack(pady=(12, 8))

        info_frame = tk.Frame(self.root)
        info_frame.pack(fill="x", padx=24, pady=(0, 4))

        tk.Label(info_frame, text="対象URL:", font=("Meiryo", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=3
        )
        tk.Label(info_frame, text=COLLECTIONS_URL, font=("Meiryo", 10)).grid(
            row=0, column=1, sticky="w", pady=3
        )
        tk.Label(info_frame, text="場所:", font=("Meiryo", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=3
        )
        location_select_frame = tk.Frame(info_frame)
        location_select_frame.grid(row=1, column=1, sticky="w", pady=3)

        for location_label, location_slug in LOCATION_OPTIONS:
            var = tk.BooleanVar(value=True)
            self.location_vars[location_slug] = var
            ttk.Checkbutton(
                location_select_frame,
                text=location_label,
                variable=var,
            ).pack(side="left", padx=(0, 12))

        tk.Label(
            info_frame,
            text="未選択の場合は実行できません",
            font=("Meiryo", 9),
            fg="gray",
        ).grid(
            row=2, column=1, sticky="w", pady=3
        )

        season_frame = tk.LabelFrame(self.root, text="シーズン選択", font=("Meiryo", 10, "bold"))
        season_frame.pack(fill="both", expand=True, padx=24, pady=(8, 8))

        list_frame = tk.Frame(season_frame)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.season_listbox = tk.Listbox(
            list_frame,
            selectmode="extended",
            height=10,
            font=("Meiryo", 10),
            exportselection=False,
        )
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.season_listbox.yview)
        self.season_listbox.configure(yscrollcommand=scrollbar.set)
        self.season_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        button_frame = tk.Frame(season_frame)
        button_frame.pack(fill="x", padx=10, pady=(0, 8))

        tk.Button(button_frame, text="全選択", width=12, command=self.select_all_seasons).pack(
            side="left", padx=4
        )
        tk.Button(button_frame, text="選択解除", width=12, command=self.clear_seasons).pack(
            side="left", padx=4
        )
        tk.Button(button_frame, text="最新シーズンを読み込む", width=20, command=self.refresh_seasons).pack(
            side="left", padx=4
        )

        option_frame = tk.Frame(self.root)
        option_frame.pack(fill="x", padx=24, pady=(0, 6))

        tk.Label(
            option_frame,
            text="取得範囲: 各シーズンの選択場所一覧1ページ目に表示されているブランドのみ",
            font=("Meiryo", 10),
            anchor="w",
        ).pack(fill="x")

        self.save_empty_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(option_frame, text="0件でもCSVを保存", variable=self.save_empty_var).pack(
            anchor="w", pady=(4, 0)
        )

        email_frame = tk.LabelFrame(self.root, text="完了メール", font=("Meiryo", 10, "bold"))
        email_frame.pack(fill="x", padx=24, pady=(4, 8))

        email_settings = load_email_settings()
        self.email_enabled_var = tk.BooleanVar(value=bool(email_settings.get("enabled")))
        self.email_to_var = tk.StringVar(value=str(email_settings.get("to_email", "")))
        self.email_from_var = tk.StringVar(value=str(email_settings.get("from_email", "")))
        self.smtp_host_var = tk.StringVar(value=str(email_settings.get("smtp_host", "smtp.gmail.com")))
        self.smtp_port_var = tk.StringVar(value=str(email_settings.get("smtp_port", "587")))
        self.smtp_user_var = tk.StringVar(value=str(email_settings.get("smtp_user", "")))
        self.smtp_tls_var = tk.BooleanVar(value=bool(email_settings.get("use_tls", True)))

        ttk.Checkbutton(
            email_frame,
            text="完了時にメールを送信",
            variable=self.email_enabled_var,
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4))

        tk.Label(email_frame, text="宛先:", font=("Meiryo", 9)).grid(row=1, column=0, sticky="e", padx=6, pady=3)
        tk.Entry(email_frame, textvariable=self.email_to_var, width=34, font=("Meiryo", 9)).grid(row=1, column=1, sticky="w", padx=6, pady=3)
        tk.Label(email_frame, text="送信元:", font=("Meiryo", 9)).grid(row=1, column=2, sticky="e", padx=6, pady=3)
        tk.Entry(email_frame, textvariable=self.email_from_var, width=34, font=("Meiryo", 9)).grid(row=1, column=3, sticky="w", padx=6, pady=3)

        tk.Label(email_frame, text="SMTP:", font=("Meiryo", 9)).grid(row=2, column=0, sticky="e", padx=6, pady=3)
        tk.Entry(email_frame, textvariable=self.smtp_host_var, width=34, font=("Meiryo", 9)).grid(row=2, column=1, sticky="w", padx=6, pady=3)
        tk.Label(email_frame, text="Port:", font=("Meiryo", 9)).grid(row=2, column=2, sticky="e", padx=6, pady=3)
        tk.Entry(email_frame, textvariable=self.smtp_port_var, width=10, font=("Meiryo", 9)).grid(row=2, column=3, sticky="w", padx=6, pady=3)

        tk.Label(email_frame, text="SMTPユーザー:", font=("Meiryo", 9)).grid(row=3, column=0, sticky="e", padx=6, pady=3)
        tk.Entry(email_frame, textvariable=self.smtp_user_var, width=34, font=("Meiryo", 9)).grid(row=3, column=1, sticky="w", padx=6, pady=3)
        tk.Label(email_frame, text="パスワード:", font=("Meiryo", 9)).grid(row=3, column=2, sticky="e", padx=6, pady=3)
        tk.Entry(email_frame, textvariable=self.email_password_var, width=24, font=("Meiryo", 9), show="*").grid(row=3, column=3, sticky="w", padx=6, pady=3)

        ttk.Checkbutton(email_frame, text="TLSを使用", variable=self.smtp_tls_var).grid(
            row=4, column=1, sticky="w", padx=6, pady=(2, 8)
        )
        tk.Label(
            email_frame,
            text="宛先・SMTP設定は保存します。パスワードは保存しません。",
            font=("Meiryo", 8),
            fg="gray",
        ).grid(row=4, column=2, columnspan=2, sticky="w", padx=6, pady=(2, 8))

        self.status_label = tk.Label(self.root, text="待機中", font=("Meiryo", 10), fg="gray")
        self.status_label.pack(pady=(4, 6))

        action_frame = tk.Frame(self.root)
        action_frame.pack(pady=(2, 14))

        self.run_button = tk.Button(
            action_frame,
            text="実行",
            font=("Meiryo", 13, "bold"),
            width=14,
            height=1,
            command=self.start_scraping,
        )
        self.run_button.pack(side="left", padx=8)

        self.stop_button = tk.Button(
            action_frame,
            text="停止",
            font=("Meiryo", 12, "bold"),
            width=12,
            height=1,
            state="disabled",
            command=self.stop_scraping,
        )
        self.stop_button.pack(side="left", padx=8)

        self.populate_seasons()
        self.select_all_seasons()

    def populate_seasons(self):
        self.season_listbox.delete(0, tk.END)

        for label, slug in self.seasons:
            self.season_listbox.insert(tk.END, f"{label} ({slug})")

    def select_all_seasons(self):
        self.season_listbox.select_set(0, tk.END)

    def clear_seasons(self):
        self.season_listbox.selection_clear(0, tk.END)

    def refresh_seasons(self):
        if self.is_running:
            messagebox.showwarning("実行中", "取得中は最新シーズンを読み込めません。")
            return

        self.status_label.config(text="最新シーズンを読み込んでいます。", fg="blue")
        self.root.update_idletasks()

        try:
            self.seasons = fetch_seasons_from_site()
            self.populate_seasons()
            self.select_all_seasons()
            self.status_label.config(text="最新シーズンを読み込みました。", fg="green")
        except Exception as e:
            self.status_label.config(text="最新シーズンの読み込みに失敗しました。既定リストを使用します。", fg="orange")
            messagebox.showwarning("最新シーズンの読み込み失敗", str(e))

    def build_config_from_form(self) -> ScrapeConfig | None:
        selected_indexes = self.season_listbox.curselection()

        if not selected_indexes:
            messagebox.showerror("入力エラー", "取得するシーズンを1つ以上選択してください。")
            return None

        selected_seasons = [self.seasons[i] for i in selected_indexes]
        selected_locations = [
            (label, slug)
            for label, slug in LOCATION_OPTIONS
            if self.location_vars.get(slug) and self.location_vars[slug].get()
        ]

        if not selected_locations:
            messagebox.showerror("入力エラー", "取得する場所を1つ以上選択してください。")
            return None

        return ScrapeConfig(
            seasons=selected_seasons,
            locations=selected_locations,
            save_empty_csv=self.save_empty_var.get(),
        )

    def get_email_settings_from_form(self) -> dict:
        return {
            "enabled": self.email_enabled_var.get(),
            "to_email": self.email_to_var.get().strip(),
            "from_email": self.email_from_var.get().strip(),
            "smtp_host": self.smtp_host_var.get().strip(),
            "smtp_port": self.smtp_port_var.get().strip(),
            "smtp_user": self.smtp_user_var.get().strip(),
            "use_tls": self.smtp_tls_var.get(),
        }

    def validate_email_settings(self) -> bool:
        settings = self.get_email_settings_from_form()

        if not settings["enabled"] or not settings["to_email"]:
            settings["enabled"] = False
            save_email_settings(settings)
            return True

        required_fields = [
            ("送信元", settings["from_email"]),
            ("SMTP", settings["smtp_host"]),
            ("Port", settings["smtp_port"]),
            ("SMTPユーザー", settings["smtp_user"]),
            ("パスワード", self.email_password_var.get()),
        ]
        missing = [label for label, value in required_fields if not str(value).strip()]

        if missing:
            messagebox.showerror("メール設定エラー", "完了メールを送る場合は以下を入力してください。\n\n" + "\n".join(missing))
            return False

        if not settings["smtp_port"].isdigit():
            messagebox.showerror("メール設定エラー", "Portは半角数字で入力してください。")
            return False

        save_email_settings(settings)
        return True

    def start_scraping(self):
        if self.is_running:
            messagebox.showwarning("実行中", "現在処理中です。完了までお待ちください。")
            return

        config = self.build_config_from_form()

        if config is None:
            return

        if not self.validate_email_settings():
            return

        confirm = messagebox.askokcancel(
            "実行確認",
            "Fashion Pressのコレクション一覧からブランド概要を取得します。\n\n"
            f"場所: {'・'.join(label for label, _slug in config.locations)}\n"
            f"シーズン数: {len(config.seasons)}\n"
            "取得範囲: 各シーズンの選択場所一覧1ページ目のみ\n"
            "次ページは取得しません。\n\n"
            "実行してよろしいですか？",
        )

        if not confirm:
            return

        self.is_running = True
        self.stop_event = threading.Event()
        self.run_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.status_label.config(text="実行中です。しばらくお待ちください。", fg="blue")

        thread = threading.Thread(target=self.scraping_worker, args=(config,), daemon=True)
        thread.start()

    def stop_scraping(self):
        if not self.is_running or self.stop_event is None:
            return

        self.stop_event.set()
        self.stop_button.config(state="disabled")
        self.status_label.config(text="停止要求を受け付けました。現在のページが終わるまでお待ちください。", fg="orange")

    def scraping_worker(self, config: ScrapeConfig):
        try:
            result = run_scraping(config, self.stop_event)
            self.root.after(0, self.on_success, result)
        except Exception as e:
            log(f"全体処理でエラーが発生しました: {e}")
            self.root.after(0, self.on_error, str(e))

    def on_success(self, result: dict):
        self.is_running = False
        self.stop_event = None
        self.run_button.config(state="normal")
        self.stop_button.config(state="disabled")

        stopped = bool(result.get("停止しました"))
        self.status_label.config(
            text="停止しました。" if stopped else "完了しました。",
            fg="orange" if stopped else "green",
        )

        message = (
            ("停止しました。\n\n" if stopped else "処理が完了しました。\n\n")
            + f"一覧取得ページ数: {result.get('一覧取得ページ数', 0)}\n"
            + f"ブランド詳細取得ページ数: {result.get('ブランド詳細取得ページ数', 0)}\n"
            + f"手動履歴CSV取り込み件数: {result.get('手動履歴CSV取り込み件数', 0)}\n"
            + f"履歴スキップ件数: {result.get('履歴スキップ件数', 0)}\n"
            + f"CSV出力件数: {result.get('CSV出力件数', 0)}\n"
            + f"CSV保存: {'あり' if result.get('CSV保存済み') else 'なし'}\n"
            + f"CSV保存先:\n{result.get('CSV保存先', '')}"
        )

        email_settings = self.get_email_settings_from_form()

        if email_settings.get("enabled") and email_settings.get("to_email"):
            try:
                send_completion_email(email_settings, self.email_password_var.get(), result)
                message += "\n\n完了メール: 送信しました"
            except Exception as e:
                log(f"完了メール送信に失敗しました: {e}")
                message += f"\n\n完了メール: 送信失敗\n{e}"

        messagebox.showinfo("取得完了" if not stopped else "停止完了", message)

    def on_error(self, error_message: str):
        self.is_running = False
        self.stop_event = None
        self.run_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.status_label.config(text="エラーが発生しました。", fg="red")
        messagebox.showerror("エラー発生", f"処理中にエラーが発生しました。\n\n{error_message}")


def main():
    root = tk.Tk()
    FashionPressCollectionsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
