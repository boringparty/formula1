import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


DRIVERS_URL = "https://www.formula1.com/en/drivers"
API_URL = "https://api.jolpi.ca/ergast/f1/2026/drivers.json?limit=100"

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
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", value).strip().lower()


def get_page(session, url):
    response = session.get(
        url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def fetch_api_drivers(session):
    print("Fetching driver data from Jolpica API...")

    response = session.get(
        API_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    api_drivers = data["MRData"]["DriverTable"]["Drivers"]

    drivers = {}

    for driver in api_drivers:
        name = (
            f"{driver['givenName']} "
            f"{driver['familyName']}"
        )

        key = normalize_name(name)

        drivers[key] = {
            "name": name,
            "code": driver.get("code"),
        }

    print(f"  Found {len(drivers)} API drivers")

    return drivers


def format_f1_image_url(url):
    if not url or "media.formula1.com" not in url:
        return url

    # Find the image asset, skipping any existing transformations.
    match = re.search(
        r"/image/upload(?:,[^/]*)?/(.*)$",
        url,
    )

    if not match:
        return url

    asset_path = match.group(1)

    # Remove existing transformation segments.
    parts = asset_path.split("/")
    asset_index = next(
        (
            i for i, part in enumerate(parts)
            if part.startswith("d_common:")
        ),
        None,
    )

    if asset_index is not None:
        asset_path = "/".join(parts[asset_index:])

    # Keep the fallback asset, version, and driver image path.
    # Change only the final image extension to JPG.
    asset_path = re.sub(
        r"\.(?:webp|png|jpeg)(?=($|\?))",
        ".jpg",
        asset_path,
        flags=re.IGNORECASE,
    )

    return (
        "https://media.formula1.com/image/upload/"
        "f_jpg/c_lfill,w_64,h_64,g_north/q_100/"
        + asset_path
    )


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

    return format_f1_image_url(
        urljoin(BASE_URL, url)
    )


def scrape_permanent_number(session, profile_url):
    html = get_page(session, profile_url)
    soup = BeautifulSoup(html, "html.parser")

    # Check embedded page data first.
    for script in soup.find_all("script"):
        content = script.string or script.get_text()

        if not content:
            continue

        content = content.strip()

        if not content.startswith(("{", "[")):
            continue

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue

        number = find_permanent_number(data)

        if number is not None:
            return number

    # Fallback to visible profile text.
    page_text = soup.get_text(" ", strip=True)

    patterns = [
        r"Permanent Number\s*#?\s*(\d{1,2})\b",
        r"Driver Number\s*#?\s*(\d{1,2})\b",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            page_text,
            flags=re.IGNORECASE,
        )

        if match:
            return int(match.group(1))

    return None


def find_permanent_number(data):
    number_keys = {
        "permanentnumber",
        "permanent_number",
        "drivernumber",
        "driver_number",
    }

    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in number_keys:
                if str(value).isdigit():
                    return int(value)

        for value in data.values():
            result = find_permanent_number(value)

            if result is not None:
                return result

    elif isinstance(data, list):
        for value in data:
            result = find_permanent_number(value)

            if result is not None:
                return result

    return None


def scrape_f1_drivers(session):
    print("Scraping Formula1.com driver directory...")

    html = get_page(session, DRIVERS_URL)
    soup = BeautifulSoup(html, "html.parser")

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

        if slug in ("", "all"):
            continue

        image = get_image_url(link)

        if not image:
            continue

        # Use the profile slug to identify the API record.
        page_name = slug.replace("-", " ")

        image_tag = link.find("img")

        if image_tag and image_tag.get("alt"):
            alt = image_tag["alt"].strip()

            if len(alt.split()) >= 2:
                page_name = alt

        page_name = re.sub(
            r"\s+",
            " ",
            page_name,
        ).strip()

        key = normalize_name(page_name)

        if key in seen:
            continue

        seen.add(key)

        # Extract constructor from the driver card.
        card_text = re.sub(
            r"\s+",
            " ",
            link.get_text(" ", strip=True),
        ).strip()

        name_position = card_text.lower().find(
            page_name.lower()
        )

        constructor = None

        if name_position >= 0:
            constructor = card_text[
                name_position + len(page_name):
            ].strip()

        if constructor:
            constructor = re.sub(
                r"\s+Flag of\s+.*$",
                "",
                constructor,
                flags=re.IGNORECASE,
            ).strip()

        profile_url = urljoin(BASE_URL, href)

        print(f"  Found {page_name} ({constructor})")

        drivers.append({
            "pageName": page_name,
            "profileUrl": profile_url,
            "constructor": constructor,
            "image": image,
        })

    return drivers


def main():
    with requests.Session() as session:
        api_drivers = fetch_api_drivers(session)

        f1_drivers = scrape_f1_drivers(session)

        output = []
        unmatched = []

        for driver in f1_drivers:
            key = normalize_name(driver["pageName"])

            api_driver = api_drivers.get(key)

            if not api_driver:
                unmatched.append(driver["pageName"])
                continue

            code = api_driver["code"]

            if not code:
                unmatched.append(
                    f"{api_driver['name']} (missing API code)"
                )
                continue

            permanent_number = scrape_permanent_number(
                session,
                driver["profileUrl"],
            )

            if permanent_number is None:
                raise RuntimeError(
                    "Could not find permanent number for "
                    f"{api_driver['name']}"
                )

            output.append({
                "name": api_driver["name"],
                "code": code,
                "permanentNumber": permanent_number,
                "constructor": driver["constructor"],
                "image": driver["image"],
            })

        if unmatched:
            raise RuntimeError(
                "Could not match all drivers to complete API data:\n"
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
