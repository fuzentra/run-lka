"""Offline tests for scripts/refresh.py using HTML shaped like the real source pages.
Run: python -m pytest -q tests   (or: python tests/test_refresh.py)
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import refresh as R  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

TODAY = dt.date(2026, 10, 14)


def run(html, running_only=True, src="Test", url="https://example.lk/events"):
    soup = BeautifulSoup(html, "html.parser")
    source = {"src": src, "url": url, "running_only": running_only}
    c = R.jsonld_candidates(soup, source, url, TODAY) or R.text_candidates(soup, source, url, TODAY)
    return [x for x in c if R.is_running(x, running_only) and (x.end or x.start) >= TODAY]


CNR = """
<ul>
 <li><img alt="Run &amp; Rave"><div><h2><a href="https://docs.google.com/forms/d/e/AAA/viewform?usp=send_form">Run &amp; Rave</a></h2>
   <p>Sat 17 Oct</p><p>Cinnamon Life at City of Dreams</p><p>More info</p>
   <a href="https://docs.google.com/forms/d/e/AAA/viewform?usp=send_form">SIGN UP</a></div></li>
 <li><img alt="Sunset Run 2026"><div><h2><a href="https://docs.google.com/forms/d/e/BBB/viewform">Sunset Run 2026</a></h2>
   <p>Sat 17 Oct</p><p>Ladies' College</p><p>More info</p><a href="https://docs.google.com/forms/d/e/BBB/viewform">SIGN UP</a></div></li>
 <li><div><h2><a href="https://singletreeevents.com/event/kandy-half-marathon-2026/">Run for Sri Lanka - Kandy Half Marathon</a></h2>
   <p>Sun 06 Dec</p><p>Kandy</p><p>More info</p></div></li>
</ul>"""

FITTMO = """
<h2>October 2026</h2><p>1 event</p>
<a href="/events/fitrun-by-fitsunday-nestomalt?utm_source=x"><div><span>OCT</span><span>25</span></div>
 <div><span>Running</span><span>LKR 1,500</span></div><h3>FIT RUN</h3>
 <p>Sun 25 Oct · 6:00 AM – 10:00 AM</p><p>Queensbury Lake Resort, Wadduwa, Wadduwa</p><p>126 going</p><p>Fit Sunday</p></a>
<a href="/events/yoga-day"><h3>Sunrise Yoga</h3><p>Sun 1 Nov · 6:00 AM</p><p>Galle Face</p></a>
"""

CCC = """
<ul><li><img src="x.webp"><h3>Let'sTALK &amp; Walk 2026</h3>
 <ul><li><img src="calendar.svg">October 10, 2026</li><li><img src="clock.svg">7:00AM - 10:00AM</li></ul>
 <p>LKR 14,500 raised so far</p><a href="https://www.1333.lk/events/3">Learn More about Let'sTALK &amp; Walk 2026</a></li>
<li><h3>Bikeathon 2026</h3><ul><li>August 29, 2026 - September 10, 2026</li></ul><a href="/events/2">Learn More</a></li>
<li><h3>Hope Walk 2026</h3><ul><li>November 21, 2026</li><li>6:30AM - 9:00AM</li></ul>
 <p>LKR 2,000 per person</p><a href="https://www.1333.lk/events/4">Learn More about Hope Walk 2026</a></li></ul>"""

JSONLD = """<script type="application/ld+json">[{"@context":"http://schema.org","@type":"Event",
"name":"Pink &#038; Strong – Charity Run/Walk","startDate":"2026-10-18T06:00:00+05:30","endDate":"2026-10-18T12:00:00+05:30",
"url":"https://singletreeevents.com/event/pink-strong-charity-run-walk/",
"location":{"@type":"Place","name":"Independence Square","address":{"addressLocality":"Colombo"}},
"offers":{"price":"3500","priceCurrency":"LKR"}},
{"@type":"Event","name":"Move to Protect: Run &amp; Walk","startDate":"2026-10-25T06:00:00+05:30",
"url":"https://singletreeevents.com/event/move-to-protect-run-walk/"}]</script>"""


def test_cnr_listing():
    c = run(CNR)
    names = {x.name: x for x in c}
    assert set(names) == {"Run & Rave", "Sunset Run 2026", "Run for Sri Lanka - Kandy Half Marathon"}, names
    assert names["Run & Rave"].start == dt.date(2026, 10, 17)
    assert names["Run & Rave"].city == "Cinnamon Life at City of Dreams"
    assert names["Run & Rave"].url == "https://docs.google.com/forms/d/e/AAA/viewform"
    assert names["Run for Sri Lanka - Kandy Half Marathon"].start == dt.date(2026, 12, 6)


def test_fittmo_filters_non_running():
    c = run(FITTMO, running_only=False, url="https://www.fittmo.com/events")
    assert [x.name for x in c] == ["FIT RUN"], [x.name for x in c]
    f = c[0]
    assert f.start == dt.date(2026, 10, 25) and f.time == "6:00 AM" and f.fee == "LKR 1,500"
    assert f.city.startswith("Queensbury Lake Resort")
    assert f.url == "https://www.fittmo.com/events/fitrun-by-fitsunday-nestomalt"


def test_ccc_walks_only_future():
    c = run(CCC, running_only=False, url="https://www.1333.lk/events")
    assert [x.name for x in c] == ["Hope Walk 2026"], [x.name for x in c]
    h = c[0]
    assert h.start == dt.date(2026, 11, 21) and h.url == "https://www.1333.lk/events/4"
    assert h.fee == "LKR 2,000" and h.time == "6:30AM"


def test_jsonld():
    c = run(JSONLD)
    assert len(c) == 2
    p = next(x for x in c if x.name.startswith("Pink"))
    assert p.start == dt.date(2026, 10, 18) and p.end is None and p.time == "6:00 AM"
    assert p.city == "Independence Square, Colombo" and p.fee == "LKR 3500"


def test_merge_matches_and_adds():
    events = [
        {"id": "sunset-run", "name": "Sunset Run 2026", "type": "fun", "start": "2026-10-17", "city": "Ladies' College, Colombo 7",
         "dist": "See event page", "org": "CNR", "about": "x", "url": "https://www.colombonightrun.com/events-1/sunset-run-2026", "src": "CNR"},
        {"id": "kandy-rfsl", "name": "Run for Sri Lanka – Kandy Half Marathon", "type": "road", "start": "2026-12-06", "city": "Kandy",
         "dist": "21.1K", "org": "ST", "about": "x", "url": "https://singletreeevents.com/event/kandy-half-marathon-2026/", "src": "ST"},
        {"id": "rft", "name": "Run for Tomorrow", "type": "fun", "start": "2026-10-24", "city": "Venue to be announced",
         "dist": "To be announced", "org": "DM", "about": "x", "url": "https://example.com/rft", "src": "x"},
        {"id": "old", "name": "Old Run", "type": "fun", "start": "2025-08-01", "city": "x", "dist": "x", "org": "x",
         "about": "x", "url": "https://x.lk", "src": "x"},
        {"id": "club", "name": "Weekly", "type": "club", "rec": 4, "from": "2026-01-01", "city": "x", "dist": "x",
         "org": "x", "about": "x", "url": "https://x.lk", "src": "x", "short": "x"},
    ]
    cands = run(CNR, url="https://www.colombonightrun.com/")
    cands.append(R.Candidate(name="Run for Tomorrow", start=dt.date(2026, 10, 31), src="Daily Mirror",
                             url="https://example.com/rft", city="Viharamahadevi Park", page_url="https://dm.lk/"))
    rep = R.Report()
    R.merge(events, cands, TODAY, rep)
    events = R.prune(events, TODAY, rep)
    ids = [e["id"] for e in events]
    assert "old" not in ids and "club" in ids
    assert len(rep.added) == 1 and "Run & Rave" in rep.added[0], rep.added
    rft = next(e for e in events if e["id"] == "rft")
    assert rft["start"] == "2026-10-31" and rft["city"] == "Viharamahadevi Park", rft
    new = next(e for e in events if e["name"] == "Run & Rave")
    assert new["type"] == "fun" and new["city"] == "Cinnamon Life at City of Dreams" and new["dist"] == "See event page"
    assert R.sort_events(events)[-1]["id"] == "club"


def test_guessers():
    assert R.guess_type("CAL Momentum duathlon") == "tri"
    assert R.guess_type("Kandy Half Marathon") == "road"
    assert R.guess_type("Pekoe trail ultra") == "trail"
    assert R.guess_dist("Half marathon, 10K and 5 km fun run") == "21.1K · 10K · 5K"
    assert R.clean_url("https://a.lk/x?utm_source=fb&id=3&fbclid=Z#top") == "https://a.lk/x?id=3"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
