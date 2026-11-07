import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


F1_URL = "https://www.formula1.com/en/drivers"
API_URL = "https://api.jolpi.ca/ergast/f1/2026/drivers.json"
API_PAGE_SIZE = 100

OUTPUT_FILE = Path("drivers.json")

# 11 teams x 2 drivers. Fail rather than save a partial grid.
EXPECTED_DRIVERS = 22

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


def normalize(value):
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]", "", value.lower())


def get_image_url(image):
    url = (
        image.get("src")
        or image.get("data-src")
        or image.get("data-lazy-src")
    )

    if not url and image.get("srcset"):
        url = (
            image["srcset"]
            .split(",")[0]
            .strip()
            .split(" ")[0]
        )

    if not url:
        return None

    url = urljoin("https://www.formula1.com", url)

    if "media.formula1.com/image/upload/" not in url:
        return url

    # Preserve the original asset path and version.
    asset = url.split("/image/upload/", 1)[1]

    asset_parts = asset.split("/")
    asset_index = next(
        (
            i for i, part in enumerate(asset_parts)
            if part.startswith("d_common:")
        ),
        None,
    )

    if asset_index is not None:
        asset = "/".join(asset_parts[asset_index:])

    # Force JPG output with the desired crop and dimensions.
    asset = re.sub(
        r"\.(webp|png|jpeg)(?=($|\?))",
        ".jpg",
        asset,
        flags=re.IGNORECASE,
    )

    return (
        "https://media.formula1.com/image/upload/"
        "f_jpg/c_lfill,w_64,h_64,g_north/q_100/"
        + asset
    )


def fetch_api_drivers(session):
    print("Fetching Jolpica driver data...")

    drivers = []
    offset = 0
    total = 0

    # The API may return fewer records than requested (it returned 30 of 36),
    # so keep paging until we have everything.
    while True:
        response = session.get(
            API_URL,
            params={"limit": API_PAGE_SIZE, "offset": offset},
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()

        mr_data = response.json()["MRData"]
        page = mr_data["DriverTable"]["Drivers"]
        total = int(mr_data["total"])

        drivers.extend(page)
        offset += len(page)

        print(f"  Fetched {len(drivers)}/{total}")

        if not page or offset >= total:
            break

    if len(drivers) < total:
        raise RuntimeError(
            f"API returned {len(drivers)} of {total} drivers."
        )

    # Index by normalized family name. Values are lists so that shared
    # surnames don't crash the script.
    indexed = {}

    for driver in drivers:
        key = normalize(driver["familyName"])
        indexed.setdefault(key, []).append(driver)

    return indexed


def find_api_driver(api_drivers, proper_name):
    parts = proper_name.split()
    candidates = api_drivers.get(normalize(parts[-1]), [])

    if len(candidates) == 1:
        return candidates[0]

    # Several drivers share this surname: match on first name instead.
    first = normalize(parts[0]) if len(parts) > 1 else ""

    if first:
        for candidate in candidates:
            if first in normalize(candidate["givenName"]):
                return candidate

    return None


def scrape_f1_drivers(session):
    print("Scraping Formula1.com driver directory...")

    response = session.get(
        F1_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    drivers = []
    seen = set()

    for link in soup.find_all("a", href=True):
        match = re.search(
            r"/en/drivers/([a-z0-9-]+)/?$",
            link["href"],
        )

        if not match:
            continue

        slug = match.group(1)

        if slug in seen:
            continue

        image = link.find("img")

        if not image:
            continue

        image_url = get_image_url(image)

        if not image_url:
            continue

        # The <img alt> is the driver's name, e.g. "Rafael Câmara".
        proper_name = (image.get("alt") or "").strip()

        if not proper_name:
            # Fallback: first/last name are the first two <p> tags.
            paragraphs = [
                p.get_text(strip=True) for p in link.find_all("p")
            ]
            proper_name = " ".join(paragraphs[:2]).strip()

        if not proper_name:
            continue

        drivers.append({
            "properName": proper_name,
            "image_url": image_url,
        })

        seen.add(slug)

        print(f"  Found {proper_name}")

    return drivers


def main():
    with requests.Session() as session:

        api_drivers = fetch_api_drivers(session)
        f1_drivers = scrape_f1_drivers(session)

        if len(f1_drivers) < EXPECTED_DRIVERS:
            raise RuntimeError(
                f"Only found {len(f1_drivers)} drivers on Formula1.com "
                f"(expected {EXPECTED_DRIVERS}). "
                "drivers.json was not changed."
            )

        output = []
        unmatched = []

        for scraped in f1_drivers:
            api_driver = find_api_driver(
                api_drivers,
                scraped["properName"],
            )

            if not api_driver:
                unmatched.append(scraped["properName"])
                continue

            output.append({
                "properName": scraped["properName"],
                "code": api_driver.get("code"),
                "fullName": (
                    f"{api_driver['givenName']} "
                    f"{api_driver['familyName']}"
                ),
                "dateOfBirth": api_driver.get("dateOfBirth"),
                "driverId": api_driver.get("driverId"),
                "permanentNumber": api_driver.get("permanentNumber"),
                "nationality": api_driver.get("nationality"),
                "image_url": scraped["image_url"],
            })

        if unmatched:
            raise RuntimeError(
                "Could not match these F1 drivers to the API:\n"
                + "\n".join(f"  - {name}" for name in unmatched)
                + "\ndrivers.json was not changed."
            )

        OUTPUT_FILE.write_text(
            json.dumps(
                output,
                indent=2,
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )

        print(
            f"\nSuccessfully saved {len(output)} drivers "
            f"to {OUTPUT_FILE}"
        )


if __name__ == "__main__":
    main()
