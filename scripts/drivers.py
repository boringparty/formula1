
#!/usr/bin/env python3
"""Maintain cumulative drivers.json using Jolpica data and F1 driver images."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SEASON = int(os.getenv("F1_SEASON", "2026"))

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.getenv("F1_DRIVERS_OUTPUT", str(REPO_ROOT / "drivers.json")))

JOLPICA = "https://api.jolpi.ca/ergast/f1"
F1_DRIVERS_URL = "https://www.formula1.com/en/drivers"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def normalize_name(value: str) -> str:
    """Normalize names for matching between the API and F1 page."""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def load_existing_drivers() -> dict:
    """Load existing records so the directory remains cumulative."""

    if not OUTPUT.exists():
        return {}

    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))

        return {
            d.get("code") or d.get("name"): d
            for d in data.get("drivers", [])
        }

    except (json.JSONDecodeError, OSError) as error:
        print(f"Warning: Could not read existing drivers.json: {error}")
        return {}


def get_image_url(image) -> str | None:
    """Extract the best available image URL from an HTML image element."""

    # Prefer the largest image in srcset, if available.
    srcset = image.get("srcset") or image.get("data-srcset")

    if srcset:
        candidates = []

        for item in srcset.split(","):
            parts = item.strip().split()

            if not parts:
                continue

            url = parts[0]
            width_match = re.search(r"(\d+)w", item)

            width = int(width_match.group(1)) if width_match else 0
            candidates.append((width, url))

        if candidates:
            return max(candidates, key=lambda item: item[0])[1]

    # Fall back to common lazy-loading attributes.
    for attribute in ("src", "data-src", "data-lazy-src"):
        url = image.get(attribute)

        if url and not url.startswith("data:"):
            return url

    return None


def scrape_f1_driver_images(session: requests.Session) -> dict:
    """
    Scrape driver image URLs from the official F1 drivers page.

    Returns:
        Dictionary keyed by normalized driver name.
    """

    response = session.get(F1_DRIVERS_URL, timeout=TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    images = {}

    # Driver cards link to individual driver profiles.
    driver_links = soup.select('a[href*="/en/drivers/"]')

    for link in driver_links:
        driver_name = link.get_text(" ", strip=True)

        if not driver_name:
            continue

        image = link.find("img")

        if not image:
            continue

        image_url = get_image_url(image)

        if not image_url:
            continue

        image_url = urljoin(F1_DRIVERS_URL, image_url)

        # Ignore links that aren't actual image assets.
        if not image_url.startswith(("http://", "https://")):
            continue

        images[normalize_name(driver_name)] = image_url

    print(f"Scraped {len(images)} driver images from Formula1.com")

    return images


def find_driver_image(full_name: str, scraped_images: dict) -> str | None:
    """Match an API driver name to a scraped F1 image."""

    normalized = normalize_name(full_name)

    # Exact match first.
    if normalized in scraped_images:
        return scraped_images[normalized]

    # Allow for extra text in the F1 driver card.
    for scraped_name, image_url in scraped_images.items():
        if normalized and normalized in scraped_name:
            return image_url

    return None


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    existing_drivers = load_existing_drivers()

    # Get driver information from Jolpica.
    response = session.get(
        f"{JOLPICA}/{SEASON}/drivers.json?limit=100",
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    drivers_list = (
        response.json()
        .get("MRData", {})
        .get("DriverTable", {})
        .get("Drivers", [])
    )

    if not drivers_list:
        raise RuntimeError("Jolpica returned no drivers. Existing file was not changed.")

    # Get driver images from Formula1.com.
    try:
        scraped_images = scrape_f1_driver_images(session)
    except requests.RequestException as error:
        print(f"Warning: F1 image scrape failed: {error}")
        print("Existing image URLs will be preserved.")
        scraped_images = {}

    updated_images = 0
    missing_images = 0

    for driver in drivers_list:
        driver_id = driver.get("driverId", "")
        code = driver.get("code") or driver.get("familyName", "???")[:3].upper()

        full_name = (
            f"{driver.get('givenName', '')} "
            f"{driver.get('familyName', '')}"
        ).strip()

        entry_key = code or full_name

        # Match the driver's image from the scraped F1 page.
        image_url = find_driver_image(full_name, scraped_images)

        if entry_key not in existing_drivers:
            existing_drivers[entry_key] = {
                "code": code,
                "name": full_name,
                "driverId": driver_id,
                "permanentNumber": driver.get("permanentNumber"),
                "nationality": driver.get("nationality"),
            }

        else:
            existing_drivers[entry_key].update({
                "driverId": driver_id,
                "permanentNumber": driver.get("permanentNumber"),
                "nationality": driver.get("nationality"),
                "name": full_name,
            })

        if image_url:
            existing_drivers[entry_key]["image_url"] = image_url
            updated_images += 1
        else:
            missing_images += 1
            print(f"Warning: No F1 image found for {full_name}")

    payload = {
        "season": SEASON,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "drivers": list(existing_drivers.values()),
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Successfully updated drivers.json\n"
        f"Drivers in directory: {len(existing_drivers)}\n"
        f"Images updated: {updated_images}\n"
        f"Images not matched: {missing_images}"
    )


if __name__ == "__main__":
    main()
