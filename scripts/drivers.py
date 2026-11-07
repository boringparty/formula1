
#!/usr/bin/env python3
"""Build drivers.json from the F1 driver listing and Jolpica API."""

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
    """Normalize names for matching between F1 and Jolpica."""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )

    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def get_image_url(image) -> str | None:
    """Extract the best available image URL."""

    # Prefer the largest image in srcset.
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

    # Check lazy-loading attributes.
    for attribute in ("src", "data-src", "data-lazy-src"):
        url = image.get(attribute)

        if url and not url.startswith("data:"):
            return url

    # Check picture source elements.
    picture = image.find_parent("picture")

    if picture:
        for source in picture.find_all("source"):
            srcset = source.get("srcset")

            if srcset:
                return srcset.split(",")[0].strip().split()[0]

    return None


def format_f1_image_url(image_url: str) -> str:
    """Apply F1 CDN crop, resizing, quality and JPG conversion."""

    parsed = urlsplit(image_url)

    if parsed.netloc not in (
        "media.formula1.com",
        "www.media.formula1.com",
    ):
        raise ValueError(f"Unexpected image host: {parsed.netloc}")

    marker = "/image/upload/"

    if marker not in parsed.path:
        raise ValueError(f"Unexpected image URL: {image_url}")

    asset_path = parsed.path.split(marker, 1)[1]

    # Preserve the original versioned asset path.
    version_match = re.search(r"(?:^|/)(v\d+/)", asset_path)

    if not version_match:
        raise ValueError(f"No version segment found: {image_url}")

    asset_path = asset_path[version_match.start():].lstrip("/")

    # Ensure the actual URL ends in .jpg.
    asset_path = re.sub(
        r"\.(webp|png|avif|jpeg|jpg)$",
        ".jpg",
        asset_path,
        flags=re.IGNORECASE,
    )

    return (
        f"https://media.formula1.com/image/upload/"
        f"{IMAGE_TRANSFORMS}/"
        f"{IMAGE_FALLBACK}/"
        f"{asset_path}"
    )


# F1 driver listing scraper -------------------------------------------------

def scrape_f1_drivers(session: requests.Session) -> list:
    """
    Scrape ONLY driver cards from the official F1 drivers listing.

    Extracts:
        name
        constructor
        image
    """

    response = session.get(F1_DRIVERS_URL, timeout=TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    drivers = []

    for link in soup.find_all("a", href=True):

        href = urlsplit(urljoin(F1_DRIVERS_URL, link["href"])).path

        # Only accept individual driver profile links.
        match = re.fullmatch(
            r"/en/drivers/([a-z0-9-]+)/?",
            href,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        slug = match.group(1)

        # Derive the driver's name from their profile URL.
        name_from_slug = slug.replace("-", " ").title()

        # Extract constructor from the driver card text.
        card_text = link.get_text(" ", strip=True)
        text_parts = card_text.split()
        name_parts = name_from_slug.split()

        constructor = None

        if (
            len(text_parts) > len(name_parts)
            and normalize_name(" ".join(text_parts[:len(name_parts)]))
            == normalize_name(name_from_slug)
        ):
            constructor = " ".join(text_parts[len(name_parts):]).strip()

        # Only use the image inside this specific driver card.
        image = link.find("img")

        if not image:
            print(f"Warning: No image found for {name_from_slug}")
            continue

        image_url = get_image_url(image)

        if not image_url:
            print(f"Warning: No image URL found for {name_from_slug}")
            continue

        image_url = urljoin(F1_DRIVERS_URL, image_url)

        try:
            image_url = format_f1_image_url(image_url)
        except ValueError as error:
            print(f"Warning: {name_from_slug}: {error}")
            continue

        drivers.append({
            "name": name_from_slug,
            "constructor": constructor,
            "image": image_url,
        })

    # Remove accidental duplicate profile links.
    unique_drivers = {}
    for driver in drivers:
        unique_drivers[normalize_name(driver["name"])] = driver

    drivers = list(unique_drivers.values())

    if not drivers:
        raise RuntimeError(
            "No driver cards found on the F1 drivers page. "
            "The page structure may have changed."
        )

    print(f"Scraped {len(drivers)} drivers from Formula1.com")

    return drivers


# Jolpica API ----------------------------------------------------------------

def fetch_jolpica_drivers(session: requests.Session) -> dict:
    """Fetch API driver information indexed by normalized full name."""

    response = session.get(
        f"{JOLPICA}/{SEASON}/drivers.json?limit=100",
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    api_drivers = (
        response.json()
        .get("MRData", {})
        .get("DriverTable", {})
        .get("Drivers", [])
    )

    if not api_drivers:
        raise RuntimeError("Jolpica returned no drivers.")

    drivers_by_name = {}

    for driver in api_drivers:
        full_name = (
            f"{driver.get('givenName', '')} "
            f"{driver.get('familyName', '')}"
        ).strip()

        drivers_by_name[normalize_name(full_name)] = driver

    print(f"Retrieved {len(api_drivers)} drivers from Jolpica")

    return drivers_by_name


# Main ----------------------------------------------------------------------

def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    # Get the current driver roster and images from F1.
    f1_drivers = scrape_f1_drivers(session)

    # Get driver codes and permanent numbers from Jolpica.
    api_drivers = fetch_jolpica_drivers(session)

    final_drivers = []

    for f1_driver in f1_drivers:

        name = f1_driver["name"]
        normalized_name = normalize_name(name)

        api_driver = api_drivers.get(normalized_name)

        if not api_driver:
            print(f"Warning: No Jolpica match for {name}")

        final_drivers.append({
            "name": (
                f"{api_driver.get('givenName', '')} "
                f"{api_driver.get('familyName', '')}"
            ).strip() if api_driver else name,

            "code": api_driver.get("code") if api_driver else None,

            "permanentNumber": (
                api_driver.get("permanentNumber")
                if api_driver else None
            ),

            "constructor": f1_driver["constructor"],

            "image": f1_driver["image"],
        })

    # Output only the drivers currently listed on Formula1.com.
    payload = {
        "season": SEASON,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "drivers": final_drivers,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "\nDriver directory updated successfully."
        f"\nDrivers: {len(final_drivers)}"
        f"\nOutput: {OUTPUT}"
    )


if __name__ == "__main__":
    main()
