
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


F1_URL = "https://www.formula1.com/en/drivers"
API_URL = "https://api.jolpi.ca/ergast/f1/2026/drivers.json?limit=100"

OUTPUT_FILE = Path("drivers.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}

TEAMS = [
    "Red Bull Racing",
    "Haas F1 Team",
    "Aston Martin",
    "Racing Bulls",
    "Mercedes",
    "Ferrari",
    "McLaren",
    "Alpine",
    "Audi",
    "Williams",
    "Cadillac",
]


def normalize(value):
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]", "", value.lower())


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

    response = session.get(
        API_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    drivers = data["MRData"]["DriverTable"]["Drivers"]

    # Index API records by normalized family name.
    indexed = {}

    for driver in drivers:
        key = normalize(driver["familyName"])

        if key in indexed:
            raise RuntimeError(
                f"Duplicate API surname: {driver['familyName']}"
            )

        indexed[key] = driver

    print(f"  Retrieved {len(drivers)} API records")

    return indexed


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
        href = link["href"]

        match = re.search(
            r"/en/drivers/([a-z0-9-]+)/?",
            href,
        )

        if not match:
            continue

        slug = match.group(1)

        if slug in seen:
            continue

        image_url = get_image_url(link)

        if not image_url:
            continue

        card_text = re.sub(
            r"\s+",
            " ",
            link.get_text(" ", strip=True),
        ).strip()

        # Identify and remove the constructor suffix.
        constructor = None

        for team in TEAMS:
            if card_text.lower().endswith(team.lower()):
                constructor = team
                proper_name = card_text[:-len(team)].strip()
                break
        else:
            print(f"  Skipping unrecognized team: {card_text}")
            continue

        if not proper_name:
            continue

        surname = normalize(proper_name.split()[-1])

        drivers.append({
            "properName": proper_name,
            "surname": surname,
            "constructor": constructor,
            "image_url": image_url,
            "slug": slug,
        })

        seen.add(slug)

        print(f"  Found {proper_name} - {constructor}")

    return drivers


def main():
    with requests.Session() as session:

        api_drivers = fetch_api_drivers(session)
        f1_drivers = scrape_f1_drivers(session)

        output = []
        unmatched = []

        for scraped in f1_drivers:
            api_driver = api_drivers.get(scraped["surname"])

            if not api_driver:
                unmatched.append(scraped["properName"])
                continue

            full_name = (
                f"{api_driver['givenName']} "
                f"{api_driver['familyName']}"
            )

            output.append({
                "properName": scraped["properName"],
                "code": api_driver.get("code"),
                "fullName": full_name,
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

        if not output:
            raise RuntimeError(
                "No drivers found. drivers.json was not changed."
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
