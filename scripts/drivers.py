
#!/usr/bin/env python3
"""Maintain cumulative drivers.json using Jolpica data and F1 driver images."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


# Configuration -------------------------------------------------------------

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

IMAGE_TRANSFORMS = "f_jpg/c_lfill,w_64,h_64,g_north/q_100"
IMAGE_FALLBACK = (
    "d_common:f1:2026:fallback:driver:"
    "2026fallbackdriverright.webp"
)


# Helpers -------------------------------------------------------------------

def normalize_name(value: str) -> str:
    """Normalize names for matching between Jolpica and Formula1.com."""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )

    value = value.lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def load_existing_drivers() -> dict:
    """Load existing records to maintain a cumulative directory."""

    if not OUTPUT.exists():
        return {}

    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))

        return {
            driver.get("code") or driver.get("name"): driver
            for driver in data.get("drivers", [])
        }

    except (json.JSONDecodeError, OSError) as error:
        print(f"Warning: Could not read existing drivers.json: {error}")
        return {}


def get_image_url(image) -> str | None:
    """Extract the best available image URL from an HTML image element."""

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

    for attribute in ("src", "data-src", "data-lazy-src"):
        url = image.get(attribute)

        if url and not url.startswith("data:"):
            return url

    return None


def format_f1_image_url(image_url: str) -> str:
    """
    Apply the standard F1 driver portrait crop and sizing.

    Preserves the original versioned asset path while replacing
    any existing CDN transformations.
    """

    parsed = urlsplit(image_url)

    if "media.formula1.com" not in parsed.netloc:
        raise ValueError(f"Unexpected F1 image host: {parsed.netloc}")

    marker = "/image/upload/"

    if marker not in parsed.path:
        raise ValueError(f"Unexpected F1 image URL: {image_url}")

    asset_path = parsed.path.split(marker, 1)[1]

    # Find the version segment, e.g. v1740000001.
    # Everything from this point onward is the original asset path.
    version_match = re.search(r"(?:^|/)(v\d+/)", asset_path)

    if not version_match:
        raise ValueError(f"No version segment found in image URL: {image_url}")

    asset_path = asset_path[version_match.start():].lstrip("/")

    return (
        f"https://media.formula1.com/image/upload/"
        f"{IMAGE_TRANSFORMS}/"
        f"{IMAGE_FALLBACK}/"
        f"{asset_path}"
    )


# Formula1.com image scraper ------------------------------------------------

def scrape_f1_driver_images(session: requests.Session) -> dict:
    """
    Scrape driver image URLs from the official F1 drivers page.

    Returns a dictionary keyed by normalized driver name.
    """

    response = session.get(F1_DRIVERS_URL, timeout=TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    images = {}

    driver_links = soup.select('a[href*="/en/drivers/"]')

    for link in driver_links:
        href = link.get("href", "")

        # Prefer an image inside the driver link.
        image = link.find("img")

        # Some layouts place the image alongside the link.
        if not image and link.parent:
            image = link.parent.find("img")

        if not image:
            continue

        image_url = get_image_url(image)

        if not image_url:
            continue

        image_url = urljoin(F1_DRIVERS_URL, image_url)

        # Only accept Formula1.com CDN images.
        if "media.formula1.com" not in urlsplit(image_url).netloc:
            continue

        # Use the profile URL slug as the primary name source.
        # Example: /en/drivers/fernando-alonso
        slug = href.rstrip("/").split("/")[-1]
        slug = re.sub(r"\.html$", "", slug)
        driver_name = slug.replace("-", " ")

        # Prefer useful image alt text when it identifies the driver.
        alt = image.get("alt", "").strip()

        if alt and len(normalize_name(alt).split()) >= 2:
            driver_name = alt

        normalized = normalize_name(driver_name)

        if normalized:
            images[normalized] = image_url

    print(
        f"Scraped {len(images)} driver image URLs "
        f"from Formula1.com"
    )

    return images


def match_driver_image(
    full_name: str,
    scraped_images: dict,
    unique_surnames: set,
) -> str | None:
    """Match a Jolpica driver to a scraped F1 image."""

    normalized = normalize_name(full_name)

    # Exact full-name match.
    if normalized in scraped_images:
        return scraped_images[normalized]

    # Fallback to surname only when it is unique in the API roster.
    surname = normalized.split()[-1] if normalized else ""

    if surname in unique_surnames:
        for scraped_name, image_url in scraped_images.items():
            if scraped_name.split()[-1] == surname:
                return image_url

    return None


# Main ----------------------------------------------------------------------

def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    # Load the existing cumulative directory.
    existing_drivers = load_existing_drivers()

    # Fetch driver information from Jolpica.
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
        raise RuntimeError(
            "Jolpica returned no drivers. Existing file was not changed."
        )

    print(f"Retrieved {len(drivers_list)} drivers from Jolpica.")

    # Scrape images from Formula1.com.
    try:
        scraped_images = scrape_f1_driver_images(session)

    except requests.RequestException as error:
        print(f"Warning: F1 image scrape failed: {error}")
        print("Existing image URLs will be preserved.")
        scraped_images = {}

    # Identify surnames that are unique in the current API roster.
    surnames = [
        normalize_name(
            f"{driver.get('givenName', '')} "
            f"{driver.get('familyName', '')}"
        ).split()[-1]
        for driver in drivers_list
        if driver.get("familyName")
    ]

    unique_surnames = {
        surname for surname in surnames
        if surnames.count(surname) == 1
    }

    updated_images = 0
    missing_images = 0

    for driver in drivers_list:
        driver_id = driver.get("driverId", "")

        code = (
            driver.get("code")
            or driver.get("familyName", "???")[:3].upper()
        )

        full_name = (
            f"{driver.get('givenName', '')} "
            f"{driver.get('familyName', '')}"
        ).strip()

        entry_key = code or full_name

        # Match the driver's image from the scraped F1 page.
        image_url = match_driver_image(
            full_name,
            scraped_images,
            unique_surnames,
        )

        # Create or update the API-sourced driver record.
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
                "name": full_name,
                "driverId": driver_id,
                "permanentNumber": driver.get("permanentNumber"),
                "nationality": driver.get("nationality"),
            })

        # Apply the F1 CDN transformations to the scraped image.
        if image_url:
            try:
                existing_drivers[entry_key]["image_url"] = (
                    format_f1_image_url(image_url)
                )
                updated_images += 1

            except ValueError as error:
                print(f"Warning: {full_name}: {error}")
                missing_images += 1

        else:
            missing_images += 1
            print(f"Warning: No F1 image found for {full_name}")

    # Build and save the cumulative directory.
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
        "\nSuccessfully updated drivers.json\n"
        f"Drivers in directory: {len(existing_drivers)}\n"
        f"Images updated: {updated_images}\n"
        f"Images not matched: {missing_images}"
    )


if __name__ == "__main__":
    main()
