# Sri Lanka – Run Calendar

Live page: https://fuzentra.github.io/run-lka/

A not-for-profit initiative by fuzentra (Pvt) Ltd. Running events in Sri Lanka, refreshed every night by GitHub Actions. No AI and no API keys are involved.

## How it works
- `data/events.json` is the master event list. Edit it by hand any time: add perks, fix details, or add events the scraper missed. Saving it rebuilds and republishes the page automatically.
- `scripts/refresh.py` runs nightly at 2:45 AM Sri Lanka time. It reads the organiser and listing sites in `SOURCES`, adds new running events, moves dates that changed, fills in missing venues, times and fees, and removes events that ended more than 13 months ago. It never deletes details you wrote.
- Auto-added events get a short generic description. Edit `about`, `dist`, `type` or `org` in `data/events.json` to improve them.
- `scripts/check_page.py` blocks any run that would break the page; the previous version stays live.
- `.github/workflows/nightly-refresh.yml` runs the steps above, commits, and publishes to GitHub Pages.

## Event format (data/events.json)
```json
{"id": "kebab-case-id", "name": "...", "type": "road | fun | tri | trail | club",
 "start": "YYYY-MM-DD", "end": "YYYY-MM-DD (optional)", "city": "venue, area", "time": "(optional)",
 "dist": "...", "fee": "(optional)", "org": "...", "about": "...", "url": "https://...", "src": "...",
 "reg": "(optional) e.g. Closes 30 Nov"}
```
Weekly club runs use `"rec": 0-6` (0 = Sunday), `"from": "YYYY-MM-DD"` and `"short": "chip label"` instead of `start`.

## Run locally
```
pip install -r requirements.txt
python scripts/refresh.py --dry-run     # show what the sources contain, change nothing
python scripts/refresh.py               # full refresh
python tests/test_refresh.py            # parser tests
```
