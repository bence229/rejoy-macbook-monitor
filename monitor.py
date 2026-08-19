import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests


REJOY_BASE = "https://rejoy.hu"
STATE_FILE = "seen_products.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    )
}


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current_href = None
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attributes = dict(attrs)
            self.current_href = attributes.get("href")
            self.current_text = []

    def handle_data(self, data):
        if self.current_href:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current_href:
            text = " ".join(self.current_text).strip()
            self.links.append((self.current_href, text))
            self.current_href = None
            self.current_text = []


def get(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def load_state():
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )


def find_candidate_products():
    candidates = set()

    # A Rejoy laptop-lista több oldalon keresztül lapozható.
    # 20 oldal bőven lefedi a jelenlegi laptopkínálatot.
    for page in range(1, 21):
        url = f"{REJOY_BASE}/shop/?page={page}"

        try:
            html = get(url)
        except Exception as error:
            print(f"Listaoldal hiba ({page}): {error}")
            continue

        parser = LinkParser()
        parser.feed(html)

        for href, text in parser.links:
            if not href:
                continue

            full_url = urljoin(REJOY_BASE, href)
            lower = full_url.lower()

            # Csak MacBook Air 13 termékoldalak.
            if "/shop/apple/" not in lower:
                continue

            if "macbook-air-13" not in lower:
                continue

            # Csak M3 vagy M4.
            if not re.search(r"-m3-|[-_]m3[-_]", lower) and not re.search(
                r"-m4-|[-_]m4[-_]", lower
            ):
                continue

            # Csak 16 GB.
            if "16-gb" not in lower:
                continue

            # Csak 256 vagy 512 GB.
            if "256gb" not in lower and "512gb" not in lower:
                continue

            candidates.add(full_url)

    return sorted(candidates)


def product_is_available(html):
    text = re.sub(r"\s+", " ", html)

    # Ha ezt látjuk, biztosan kifogyott.
    if "Tudni szeretném mikor lesz ismét raktáron!" in text:
        return False

    # Készleten lévő Rejoy-termék.
    if "Kosárba" in text:
        return True

    return False


def get_product_title(html):
    parser = LinkParser()
    parser.feed(html)

    # Megpróbáljuk a <title> tartalmát kinyerni egyszerű regexszel.
    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        html,
        re.IGNORECASE | re.DOTALL,
    )

    if match:
        title = re.sub(r"<.*?>", "", match.group(1))
        title = re.sub(r"\s+", " ", title).strip()

        if title:
            return title

    return "MacBook Air 13"


def send_telegram(message):
    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )

    response.raise_for_status()


def main():
    print("===================================")
    print("REJOY MACBOOK MONITOR")
    print("===================================")

    old_state = load_state()

    candidates = find_candidate_products()

    print(
        f"Megtalált megfelelő termékoldalak: "
        f"{len(candidates)}"
    )

    current_state = {}

    for url in candidates:
        try:
            html = get(url)
            available = product_is_available(html)

            current_state[url] = available

            status = "KÉSZLETEN" if available else "NINCS KÉSZLETEN"

            print(f"{status}: {url}")

        except Exception as error:
            print(f"Hiba: {url}")
            print(error)

    # ELSŐ FUTÁS:
    #
    # Felvesszük az aktuális állapotot, de NEM küldünk
    # értesítést a már most meglévő gépekről.
    if old_state is None:
        save_state(current_state)

        print()
        print(
            "Első futás: az aktuális készletet elmentettem."
        )
        print(
            "Most nem küldök Telegram értesítést."
        )
        print(
            "A következő futásoktól az újonnan megjelenő "
            "gépeket fogom jelezni."
        )

        return

    new_products = []

    for url, available_now in current_state.items():
        available_before = old_state.get(url, False)

        # Ez a fontos feltétel:
        #
        # korábban NEM volt készleten
        # most pedig KÉSZLETEN van.
        #
        # Vagy teljesen új termékoldal jelent meg.
        if available_now and not available_before:
            new_products.append(url)

    save_state(current_state)

    if not new_products:
        print("Nincs új megfelelő MacBook.")
        return

    print(
        f"Új megfelelő készülékek: "
        f"{len(new_products)}"
    )

    for url in new_products:
        try:
            html = get(url)
            title = get_product_title(html)

            message = (
                "🚨 ÚJ REJOY MACBOOK! 🚨\n\n"
                f"{title}\n\n"
                "✅ MacBook Air 13″\n"
                "✅ M3 vagy M4\n"
                "✅ 16 GB RAM\n"
                "✅ 256 vagy 512 GB\n"
                "✅ Jelenleg készleten\n\n"
                "🛒 TERMÉK MEGNYITÁSA:\n"
                f"{url}"
            )

            send_telegram(message)

            print(
                f"Telegram értesítés elküldve: {url}"
            )

        except Exception as error:
            print(
                f"Telegram küldési hiba: {error}"
            )


if __name__ == "__main__":
    main()
