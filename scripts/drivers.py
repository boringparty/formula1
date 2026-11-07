import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


DRIVERS_URL = "https://www.formula1.com/en/drivers"
BASE_URL = "https://www.formula1.com"
OUTPUT_FILE = Path("drivers.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


def normalize_name(value):
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value).strip().lower()


def get_page(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def format_f1_image_url(url):
    if not url or "media.formula1.com" not in url:
        return url

    # Keep the original asset path and version, but request a JPG.
    url = re.sub(r"/f_[^,/]+", "", url)
    url = re.sub(r"/c_[^,/]+", "", url)
    url = re.sub(r"/q_[^,/]+", "", url)

    url = url.replace(
        "/image/upload/",
        "/image/upload/f_jpg/c_lfill,w_64,h_64,g_north/q_100/",
        1,
    )

    # Change only the final image extension.
    url = re.sub(r"\.(?:webp|png|jpeg)(?=($|\?))", ".jpg", url, flags=re.I)

    return url


def get_image_url(link):
    image = link.find("img")

    if not image:
        return None

    url = (
        image.get("src")
        or image.get("data-src")
        or image.get("data-lazy-src")
    )

    if not url and image.get("srcset"):
        url = image["srcset"].split(",")[0].strip().split(" ")[0]

    if not url:
        return None

    return format_f1_image_url(urljoin(BASE_URL, url))


def extract_driver_code(data, driver_name):
    """
    Search embedded page data for a driver code.
    Falls back to the first three letters of the surname.
    """

    name_key = normalize_name(driver_name)
    matches = []

    def walk(value):
        if isinstance(value, dict):
            # Look for objects containing both a driver name and code.
            name_values = [
                str(v)
                for k, v in value.items()
                if k.lower() in ("name", "fullname", "full_name", "drivername")
            ]

            if any(normalize_name(n) == name_key for n in name_values):
                for key, item in value.items():
                    if key.lower() in (
                        "code",
                        "drivercode",
                        "driver_code",
                        "abbreviation",
                    ):
                        if isinstance(item, str) and item.strip():
                            matches.append(item.strip().upper())

            for item in value.values():
                walk(item)

        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)

    if matches:
        return matches[0]

    surname = driver_name.split()[-1]
    return normalize_name(surname).replace(" ", "")[:3].upper()


def extract_embedded_data(soup):
    """Collect JSON embedded in the page."""

    data = []

    for script in soup.find_all("script"):
        content = script.string or script.get_text()

        if not content:
            continue

        content = content.strip()

        if not content.startswith(("{", "[")):
            continue

        try:
            data.append(json.loads(content))
        except (json.JSONDecodeError, TypeError):
            continue

    return data


def scrape_driver_profile(session, profile_url, driver_name, constructor):
    """
    Read the driver's permanent number from their Formula1.com profile.
    """

    html = get_page(session, profile_url)
    soup = BeautifulSoup(html, "html.parser")

    page_text = soup.get_text(" ", strip=True)

    # On the profile page, the number appears after the driver's team.
    pattern = (
        re.escape(driver_name)
        + r".*?"
        + re.escape(constructor)
        + r"\s+(\d{1,2})\b"
    )

    match = re.search(pattern, page_text, flags=re.IGNORECASE)

    if match:
        return int(match.group(1))

    # Fallback: inspect embedded page data for a permanent number.
    embedded_data = extract_embedded_data(soup)

    number_keys = (
        "permanentnumber",
        "permanent_number",
        "drivernumber",
        "driver_number",
        "number",
    )

    def find_number(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in number_keys:
                    if str(item).isdigit():
                        return int(item)

            for item in value.values():
                result = find_number(item)
                if result is not None:
                    return result

        elif isinstance(value, list):
            for item in value:
                result = find_number(item)
                if result is not None:
                    return result

        return None

    for item in embedded_data:
        number = find_number(item)
        if number is not None:
            return number

    return None


def scrape_f1_drivers(session):
    print("Scraping current F1 driver directory...")

    html = get_page(session, DRIVERS_URL)
    soup = BeautifulSoup(html, "html.parser")
    embedded_data = extract_embedded_data(soup)

    drivers = []
    seen = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]

        match = re.search(r"/en/drivers/([a-z0-9-]+)/?", href)

        if not match:
            continue

        slug = match.group(1)

        if slug in ("", "all"):
            continue

        image = get_image_url(link)

        if not image:
            continue

        name = slug.replace("-", " ").title()

        image_tag = link.find("img")
        if image_tag and image_tag.get("alt"):
            alt = image_tag["alt"].strip()

            if len(alt.split()) >= 2:
                name = alt

        name = re.sub(r"\s+", " ", name).strip()

        if normalize_name(name) in seen:
            continue

        seen.add(normalize_name(name))

        # The directory card contains the driver name followed by constructor.
        card_text = link.get_text(" ", strip=True)
        card_text = re.sub(r"\s+", " ", card_text).strip()

        name_position = card_text.lower().find(name.lower())

        constructor = None

        if name_position >= 0:
            constructor = card_text[
                name_position + len(name):
            ].strip()

        # Remove accessibility text associated with national flags.
        if constructor:
            constructor = re.sub(
                r"\s+Flag of\s+.*$",
                "",
                constructor,
                flags=re.IGNORECASE,
            ).strip()

        profile_url = urljoin(BASE_URL, href)

        print(f"  Found {name} ({constructor})")

        # Driver code from embedded Formula1 page data.
        code = None

        for item in embedded_data:
            code = extract_driver_code(item, name)
            if code:
                break

        # Permanent number from the driver's profile page.
        permanent_number = scrape_driver_profile(
            session,
            profile_url,
            name,
            constructor or "",
        )

        drivers.append({
            "name": name,
            "code": code,
            "permanentNumber": permanent_number,
            "constructor": constructor,
            "image": image,
        })

    return drivers


def main():
    with requests.Session() as session:
        drivers = scrape_f1_drivers(session)

    if not drivers:
        raise RuntimeError("No drivers were found. drivers.json was not changed.")

    OUTPUT_FILE.write_text(
        json.dumps(drivers, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"\nSaved {len(drivers)} drivers to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
