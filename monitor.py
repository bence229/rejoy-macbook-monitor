import json
import os
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests


REJOY_BASE = "https://rejoy.hu"
REJOY_LIST_URL = "https://rejoy.hu/laptop/apple/?page={page}"
STATE_FILE = "seen_products.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Szándékosan csak néhány listaoldalt nézünk meg.
# A korábbi 20 oldal túl sok kérés volt és 429-et okozott.
PAGES_TO_CHECK = 2

# Két listaoldal között várunk egy kicsit,
# hogy ne terheljük túl a Rejoy szerverét.
DELAY_BETWEEN_PAGES = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
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

            self.links.append(
                (self.current_href, text)
            )

            self.current_href = None
            self.current_text = []


def get(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")

        print(
            "REJOY 429 Too Many Requests."
        )

        if retry_after:
            print(
                f"Retry-After: {retry_after}"
            )

        raise RuntimeError(
            "A Rejoy ideiglenesen korlátozta a lekérést."
        )

    response.raise_for_status()

    return response.text


def load_state():
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception:
        return None


def save_state(state):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )


def is_target_product(url, text):
    """
    Csak ezek érdekelnek:

    - MacBook Air 13"
    - M3 vagy M4
    - 16 GB RAM
    - 256 vagy 512 GB
    """

    combined = (
        f"{url} {text}"
    ).lower()

    # MacBook Air 13"
    if "macbook air 13" not in combined:
        return False

    # M3 vagy M4
    has_m3 = re.search(
        r"\bm3\b|[-_]m3[-_]",
        combined,
    )

    has_m4 = re.search(
        r"\bm4\b|[-_]m4[-_]",
        combined,
    )

    if not has_m3 and not has_m4:
        return False

    # 16 GB RAM
    if "16 gb" not in combined:
        return False

    # 256 vagy 512 GB
    has_256 = (
        "256 gb" in combined
        or "256gb" in combined
    )

    has_512 = (
        "512 gb" in combined
        or "512gb" in combined
    )

    if not has_256 and not has_512:
        return False

    return True


def find_available_products():
    """
    A Rejoy Apple laptop-listáját nézzük.

    Fontos:
    nem kérjük le külön a termékoldalakat.
    Ez jelentősen csökkenti a HTTP kérések számát,
    így kisebb az esélye a 429-es blokkolásnak.
    """

    candidates = {}

    for page in range(
        1,
        PAGES_TO_CHECK + 1,
    ):
        url = REJOY_LIST_URL.format(
            page=page
        )

        print()
        print(
            f"Rejoy listaoldal ellenőrzése: "
            f"{page}/{PAGES_TO_CHECK}"
        )

        try:
            html = get(url)

        except Exception as error:
            print(
                f"Listaoldal hiba: {error}"
            )

            # Ha nem tudtuk ellenőrizni az oldalt,
            # inkább megszakítjuk a teljes ellenőrzést.
            # Így nem írjuk felül tévesen az előző állapotot.
            raise

        parser = LinkParser()
        parser.feed(html)

        for href, text in parser.links:
            if not href:
                continue

            full_url = urljoin(
                REJOY_BASE,
                href,
            )

            lower_url = full_url.lower()

            # Csak Rejoy Apple termékoldal.
            if "/shop/apple/" not in lower_url:
                continue

            if not is_target_product(
                full_url,
                text,
            ):
                continue

            # A listázóoldalon szereplő terméket
            # jelenleg elérhetőnek tekintjük.
            candidates[full_url] = (
                text.strip()
                or "MacBook Air 13"
            )

        if page < PAGES_TO_CHECK:
            time.sleep(
                DELAY_BETWEEN_PAGES
            )

    return candidates


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

    try:
        products = find_available_products()

    except Exception as error:
        print()
        print(
            "Az ellenőrzés nem sikerült."
        )
        print(error)

        # Nagyon fontos:
        # sikertelen ellenőrzésnél NEM mentjük el
        # az üres/hiányos állapotot.
        return

    print()
    print(
        f"Megtalált megfelelő készleten lévő "
        f"termékek: {len(products)}"
    )

    for url, title in products.items():
        print(
            f"KÉSZLETEN: {title}"
        )
        print(url)
        print()

    current_state = {
        url: True
        for url in products
    }

    # Első sikeres futás:
    # csak elmentjük az állapotot.
    if old_state is None:
        save_state(current_state)

        print(
            "Első sikeres futás."
        )
        print(
            "Az aktuális készlet elmentve."
        )
        print(
            "Telegram értesítést most nem küldök."
        )

        return

    new_products = []

    # Az újonnan megjelent / újra készleten lévő
    # termékeket keressük.
    for url, title in products.items():
        was_available = old_state.get(
            url,
            False,
        )

        if not was_available:
            new_products.append(
                (url, title)
            )

    # Az aktuális állapot mentése.
    save_state(current_state)

    if not new_products:
        print(
            "Nincs új megfelelő MacBook."
        )
        return

    print()
    print(
        f"ÚJ megfelelő készülékek: "
        f"{len(new_products)}"
    )

    for url, title in new_products:
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

        try:
            send_telegram(message)

            print(
                "Telegram értesítés elküldve:"
            )
            print(url)

        except Exception as error:
            print(
                "Telegram küldési hiba:"
            )
            print(error)


if __name__ == "__main__":
    main()
