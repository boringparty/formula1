# 🏁 F1 Data Pipeline (2026 Season)

An automated, high-reliability Formula 1 data ingestion and CDN assets pipeline built with Python and GitHub Actions. This repository generates, enriches, and serves lightweight JSON datasets for dashboards, widgets, and external applications without relying on slow external API calls at runtime.

---

## 🏎️ Core Data Assets

| File | Description | Update Frequency | Source |
| :--- | :--- | :--- | :--- |
| **`circuits.json`** | Scraped track profiles, track map SVGs, race metadata, and fallback circuit data for the 2026 calendar. | Monthly / On-Demand | Official F1 Subpages + Fallback Map |
| **`drivers.json`** | Driver rosters, team affiliations, headshot URLs, and high-res asset transformations hosted on the official F1 CDN. | Weekly | F1 Media CDN (`DRIVER_ASSET_MAP`) |
| **`current.json`** | Operational live state for the active or next upcoming race weekend. Combines qualifying times, starting grid positions, race classifications, and driver metrics into unified per-driver records. | Every 15 minutes | Jolpica / Ergast API |
| **`season.json`** | Cumulative historical archive storing completed race weekend snapshots indexed by round number. | Weekly (Mondays) | Archived snapshots from `current.json` |

---

## 🛠️ Unified Driver Schema (`current.json`)

To eliminate complex client-side matching, `current.json` normalizes driver and team names and consolidates all active weekend sessions into flat driver records:

```json
{
  "season": 2026,
  "round": "19",
  "race_name": "Mexico City Grand Prix",
  "date": "2026-11-01",
  "time": "20:00:00Z",
  "updated_at_utc": "2026-11-01T11:28:09.599893+00:00",
  "drivers": [
    {
      "driverName": "Lewis Hamilton",
      "driverId": "hamilton",
      "code": "HAM",
      "constructorId": "ferrari",
      "constructorName": "Ferrari",
      "no": "44",
      "grid": "1",
      "qualy": "1",
      "q1": "1:17.341",
      "q2": "1:16.953",
      "q3": "1:16.514",
      "pos": "",
      "race": "",
      "laps": "",
      "pts": "",
      "status": ""
    }
```
