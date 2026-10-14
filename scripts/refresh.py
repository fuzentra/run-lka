#!/usr/bin/env python3
"""Nightly refresh for the Sri Lanka Run Calendar - no AI, no API keys.

What it does:
  1. Loads the master event list from data/events.json (you can edit that file by hand).
  2. Downloads each page in SOURCES and pulls out events (name, date, venue, link).
     It reads schema.org Event data when a site provides it, and otherwise looks
     for lines that contain a date with an event title next to them.
  3. Merges what it finds: adds new events, moves dates that changed, fills in
     missing details. It never deletes your hand-written details.
  4. Removes events that ended more than 13 months ago.
  5. Writes data/events.json and copies it into the events-data block of index.html.

Usage:
  python scripts/refresh.py               # scrape + merge + build
  python scripts/refresh.py --build-only  # just copy data/events.json into index.html
  python scripts/refresh.py --dry-run     # scrape and print, change nothing
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "events.json"
PAGE_FILE = ROOT / "index.html"
BLOCK_TAG = '<script type="application/json" id="events-data">'

SL_TZ = dt.timezone(dt.timedelta(hours=5, minutes=30))  # Asia/Colombo, no DST

# ---------------------------------------------------------------------------
# Sources. Add or remove pages here.
#   running_only: True  -> every event on the page is a running event
#                 False -> keep only events whose name/details mention running words
#   org:          organiser to use for new events from this page (optional)
# ---------------------------------------------------------------------------
SOURCES = [
    {"src": "Colombo Night Run", "url": "https://www.colombonightrun.com/", "running_only": True},
    {"src": "Colombo Night Run", "url": "https://www.colombonightrun.com/events-1", "running_only": True},
    {"src": "SingleTree Events", "url": "https://singletreeevents.com/", "running_only": True, "org": "SingleTree Events"},
    {"src": "SingleTree Events", "url": "https://singletreeevents.com/events/", "running_only": True, "org": "SingleTree Events"},
    {"src": "vforv.org", "url": "https://vforv.org/", "running_only": True, "org": "Voice Foundation (vforv)"},
    {"src": "Fittmo", "url": "https://www.fittmo.com/events", "running_only": False},
    {"src": "1333.lk", "url": "https://www.1333.lk/events", "running_only": False, "org": "CCCline 1333 (CCC Foundation)"},
    {"src": "Ahotu", "url": "https://www.ahotu.com/calendar/running/sri-lanka", "running_only": True},
    {"src": "GPASS", "url": "https://gpass.lk/", "running_only": False},
    {"src": "G360", "url": "https://www.g360.lk/", "running_only": False},
    {"src": "Tickets Ministry", "url": "https://www.ticketsministry.com/sports", "running_only": False},
    {"src": "ICA International Marathon", "url": "https://icainternationalmarathon.com/", "running_only": True,
     "org": "Indian Cultural Association"},
    {"src": "Colombo Marathon", "url": "https://colombomarathon.lk/", "running_only": True},
    {"src": "LSR Colombo Marathon", "url": "https://colombomarathon.com/", "running_only": True},
    {"src": "IRONMAN", "url": "https://www.ironman.com/races/im703-colombo", "running_only": True},
]

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 RunLankaCalendarBot/1.0 (+https://fuzentra.github.io/run-lka/)")

RUN_WORDS = re.compile(
    r"\b(run|runs|running|runners?|marathon|half[- ]marathon|walk|walkathon|walks|jog|jogging|"
    r"duathlon|triathlon|aquathlon|trail|ultra|5\s?k|10\s?k|21(?:\.1)?\s?k|42(?:\.2)?\s?k|ironman|fit ?run|"
    r"road race|fun ?run|colour run|color run)\b", re.I)
NOT_RUNNING = re.compile(
    r"\b(cricket|rugby|football|futsal|basketball|volleyball|tennis|badminton|bikeathon|cycl\w*|bike|"
    r"concert|movie|film|esports?|chess|hockey|swim(?:ming)? meet|motor|drag race|car race|horse)\b", re.I)
GENERIC = re.compile(
    r"^(sign ?up|register( now)?|registration( closed| open)?.*|more info|more details|details|learn more.*|"
    r"read more|book now|buy tickets?|get tickets?|view (event|details)|see more|share|upcoming events?|"
    r"events?|running|cycling|fitness|sports?|free|sold out|going|interested|home|menu|close|"
    r"\d+ going|\d+ events?|all events|past events|load more|next|previous)$", re.I)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MON_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DOW_RE = r"(?:(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|nesday|sday|urday)?\.?,?\s+)?"
DATE_PATTERNS = [
    # 2026-10-25
    re.compile(r"\b(?P<y>20\d\d)-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b"),
    # Sat 17 Oct | 25 October 2026 | 25th Oct, 2026
    re.compile(DOW_RE + r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MON_RE + r"\b\.?,?(?:\s+(?P<y>20\d\d))?", re.I),
    # October 10, 2026 | Sun, Oct 25, 2026 | Oct 25
    re.compile(DOW_RE + r"\b" + MON_RE + r"\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b,?(?:\s+(?P<y>20\d\d))?", re.I),
]
TIME_RE = re.compile(r"\b(\d{1,2}(?:[:.]\d{2})?\s?(?:am|pm))\b", re.I)
DIST_RE = re.compile(r"\b(\d{1,3}(?:\.\d)?)\s?(k|km)\b", re.I)
FEE_RE = re.compile(r"\b(?:LKR|Rs\.?)\s?[\d,]{3,}(?:\.\d\d)?(?:\s?[-–]\s?(?:LKR|Rs\.?)?\s?[\d,]{3,})?", re.I)
PRICE_OR_COUNT = re.compile(r"^(?:LKR|Rs\.?|USD|\$|€)\s?[\d,.]+|^\d[\d,]*\s*(going|interested|tickets?|people)\b", re.I)


@dataclass
class Candidate:
    name: str
    start: dt.date
    src: str
    url: str
    end: dt.date | None = None
    city: str = ""
    time: str = ""
    fee: str = ""
    dist: str = ""
    org: str = ""
    context: str = ""
    from_jsonld: bool = False
    page_url: str = ""


@dataclass
class Report:
    added: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    source_notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def today_sl() -> dt.date:
    return dt.datetime.now(SL_TZ).date()


def clean(s) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"\s+", " ", s).strip(" \t\n|·•-–—:")
    return s


def clean_url(url: str) -> str:
    """Drop tracking parameters (utm_*, fbclid, usp, igsh ...) and fragments."""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not (k.lower().startswith("utm_") or k.lower() in {"fbclid", "gclid", "igsh", "igshid", "usp", "ref", "mibextid"})]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def url_key(url: str) -> str:
    p = urlsplit(clean_url(url))
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/") + ("?" + p.query if p.query else "")).lower()


STOP = {"the", "a", "an", "of", "for", "by", "and", "in", "at", "to", "with", "sri", "lanka", "colombo",
        "edition", "annual", "presents", "powered", "run", "walk", "charity", "event"}


def name_tokens(name: str) -> set:
    s = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    s = s.replace("&", " and ")
    s = re.sub(r"\b20\d\d\b", " ", s)
    s = re.sub(r"\b\d+(st|nd|rd|th)\b", " ", s)
    toks = set(re.findall(r"[a-z0-9]+", s))
    core = toks - STOP
    return core or toks


def similarity(a: str, b: str) -> float:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return max(inter / len(ta | tb), inter / min(len(ta), len(tb)) * 0.9)


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60].strip("-") or "event"


def month_num(token: str) -> int:
    return MONTHS[token.lower()[:3]]


def infer_year(m: int, d: int, today: dt.date) -> dt.date | None:
    """A date shown without a year is taken as the next occurrence (allowing 45 days back)."""
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            cand = dt.date(y, m, d)
        except ValueError:
            continue
        if cand >= today - dt.timedelta(days=45):
            return cand
    return None


def find_dates(text: str, today: dt.date) -> list[tuple[dt.date, int, int]]:
    """Return (date, start_index, end_index) for each date found in text, in order."""
    found = []
    taken = []
    for pat in DATE_PATTERNS:
        for mt in pat.finditer(text):
            if any(mt.start() < e and mt.end() > s for s, e in taken):
                continue
            gd = mt.groupdict()
            try:
                if pat is DATE_PATTERNS[0]:
                    m, d, y = int(gd["m"]), int(gd["d"]), int(gd["y"])
                else:
                    mon = next(g for g in mt.groups() if g and g[:3].lower() in MONTHS)
                    m, d = month_num(mon), int(gd["d"])
                    y = int(gd["y"]) if gd.get("y") else None
                date = dt.date(y, m, d) if y else infer_year(m, d, today)
            except (ValueError, StopIteration, KeyError, TypeError):
                continue
            if date:
                found.append((date, mt.start(), mt.end()))
                taken.append((mt.start(), mt.end()))
    found.sort(key=lambda x: x[1])
    # A year written only on the second date of a range ("Aug 29 - Sep 10, 2026") is applied to the first.
    return found


def parse_iso_date(v) -> dt.date | None:
    if not v:
        return None
    m = re.match(r"(20\d\d)-(\d{2})-(\d{2})", str(v))
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def guess_type(text: str) -> str:
    t = text.lower()
    if re.search(r"duathlon|triathlon|aquathlon|ironman|70\.3", t):
        return "tri"
    if re.search(r"\btrail\b|\bultra\b|mountain run|sky ?run", t):
        return "trail"
    if re.search(r"marathon|\bhalf\b|21(\.1)?\s?k|42(\.2)?\s?k|road race", t) and not re.search(r"fun run|walk", t):
        return "road"
    return "fun"


def guess_dist(text: str) -> str:
    seen = []
    for num, _unit in DIST_RE.findall(text):
        v = num.rstrip("0").rstrip(".") if "." in num else num
        if 1 <= float(num) <= 400 and v + "K" not in seen:
            seen.append(v + "K")
    if re.search(r"half[- ]marathon", text, re.I) and "21.1K" not in seen and "21K" not in seen:
        seen.insert(0, "21.1K")
    if re.search(r"(?<!half )(?<!half-)\bmarathon\b", text, re.I) and not seen:
        seen.append("See event page")
    seen = [s for s in seen if s != "See event page"]
    seen.sort(key=lambda s: -float(s[:-1]))
    return " · ".join(seen) if seen else "See event page"


def is_running(c: Candidate, running_only: bool) -> bool:
    blob = f"{c.name} {c.context}"
    if NOT_RUNNING.search(c.name):
        return False
    if running_only:
        return True
    return bool(RUN_WORDS.search(blob)) and not NOT_RUNNING.search(c.name)


# ---------------------------------------------------------------------------
# Fetching and parsing
# ---------------------------------------------------------------------------
def fetch(url: str) -> str | None:
    import requests
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}, timeout=30)
            if r.status_code == 200 and r.text:
                r.encoding = r.encoding or "utf-8"
                return r.text
            print(f"  ! {url} -> HTTP {r.status_code}")
            if r.status_code in (403, 404, 410):
                return None
        except Exception as exc:  # network errors: retry
            print(f"  ! {url} -> {type(exc).__name__}: {exc}")
        time.sleep(3 * (attempt + 1))
    return None


def walk_json(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk_json(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_json(v)


def jsonld_candidates(soup, source: dict, page_url: str, today: dt.date) -> list[Candidate]:
    out = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in walk_json(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(isinstance(t, str) and t.endswith("Event") for t in types):
                continue
            name = clean(node.get("name"))
            start = parse_iso_date(node.get("startDate"))
            if not name or not start:
                continue
            end = parse_iso_date(node.get("endDate"))
            loc = node.get("location") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            city = ""
            if isinstance(loc, dict):
                addr = loc.get("address") or {}
                locality = clean(addr.get("addressLocality")) if isinstance(addr, dict) else clean(addr)
                parts = [clean(loc.get("name")), locality]
                city = ", ".join(dict.fromkeys(p for p in parts if p))
            elif isinstance(loc, str):
                city = clean(loc)
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            fee = ""
            if isinstance(offers, dict) and offers.get("price") not in (None, "", "0", 0):
                cur = offers.get("priceCurrency") or "LKR"
                fee = f"{cur} {offers.get('price')}"
            tm = ""
            mt = re.search(r"T(\d{2}):(\d{2})", str(node.get("startDate") or ""))
            if mt and mt.group(0) != "T00:00":
                h, mnt = int(mt[1]), int(mt[2])
                tm = f"{(h % 12) or 12}:{mnt:02d} {'AM' if h < 12 else 'PM'}"
            org = node.get("organizer") or {}
            if isinstance(org, list):
                org = org[0] if org else {}
            desc = clean(node.get("description"))[:400]
            out.append(Candidate(
                name=name, start=start, end=end if end and end > start else None,
                src=source["src"], url=clean_url(urljoin(page_url, node.get("url") or page_url)),
                city=city, time=tm, fee=fee,
                org=clean(org.get("name")) if isinstance(org, dict) else clean(org),
                context=f"{name} {desc}", from_jsonld=True, page_url=page_url))
    return out


BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer",
              "tr", "td", "th", "dt", "dd", "br", "span", "a", "time", "button", "strong", "b", "small", "label"}


def page_lines(soup, page_url: str) -> list[dict]:
    """Flatten the page into text lines, remembering link targets and headings."""
    from bs4 import NavigableString
    from bs4.element import PreformattedString
    for t in soup(["script", "style", "noscript", "svg", "template", "iframe", "head"]):
        t.decompose()
    lines: list[dict] = []
    for s in soup.find_all(string=True):
        # Skip HTML comments, doctypes and CDATA (e.g. "<!-- Page cached by LiteSpeed Cache ... on 2026-10-14 -->").
        if not isinstance(s, NavigableString) or isinstance(s, PreformattedString):
            continue
        text = clean(s)
        if not text:
            continue
        a = s.find_parent("a")
        href = a.get("href") if a else None
        if href and not href.startswith(("http", "/")):
            href = None if href.startswith(("#", "mailto:", "tel:", "javascript:")) else href
        heading = s.find_parent(re.compile(r"^h[1-6]$")) is not None
        parent = s.parent
        # Join with the previous line when both pieces sit inside the same inline element.
        if lines and lines[-1]["parent"] is parent and parent.name not in BLOCK_TAGS:
            lines[-1]["text"] += " " + text
            continue
        lines.append({"text": text, "href": urljoin(page_url, href) if href else None,
                      "heading": heading, "parent": parent, "anchor": a})
    return lines


# A title never ends mid-sentence: "Colombo will once again come alive on", "Making History at Galle Face Green! On".
DANGLING = re.compile(r"\b(on|at|in|from|by|to|until|till|of|for|and|the|a|is|are|was|will|be|held|starting|scheduled|date[ds]?)$", re.I)


def looks_like_title(line: dict) -> bool:
    t = line["text"]
    if DANGLING.search(t.rstrip(" .,:;!-–—")):
        return False
    if len(t) < 4 or len(t) > 110 or not re.search(r"[A-Za-z]{3}", t):
        return False
    if GENERIC.match(t) or PRICE_OR_COUNT.match(t) or TIME_RE.fullmatch(t):
        return False
    if find_dates(t, dt.date(2000, 1, 1)) and len(re.sub(r"[^A-Za-z]", "", t)) < 14:
        return False
    if re.fullmatch(r"[A-Za-z]{3}\s?\d{1,2}|\d{1,2}|[A-Za-z]{3}", t):  # "OCT 25", "25", "Oct"
        return False
    if t.lower().startswith(("http", "www.", "©", "copyright", "privacy", "terms")):
        return False
    return True


def text_candidates(soup, source: dict, page_url: str, today: dt.date) -> list[Candidate]:
    lines = page_lines(soup, page_url)
    out: list[Candidate] = []
    used_titles: set[int] = set()
    for i, line in enumerate(lines):
        dates = find_dates(line["text"], today)
        if not dates:
            continue
        # Ignore long paragraphs; we only want listing lines such as "Sat 17 Oct" or "October 10, 2026".
        if len(line["text"]) > 140:
            continue
        start = dates[0][0]
        end = None
        if len(dates) > 1 and re.search(r"[-–—]|\bto\b", line["text"][dates[0][2]:dates[1][1]]):
            end = dates[1][0]
            if start > end:  # "Aug 29 - Sep 10, 2026" with the year only on the second date
                try:
                    start = start.replace(year=end.year)
                except ValueError:
                    pass
            if end <= start:
                end = None

        # Title: nearest heading/link-ish title line within 6 lines (before or after).
        best = None
        for dist in range(0, 7):
            for j in ((i - dist, i + dist) if dist else (i,)):
                if j < 0 or j >= len(lines) or j in used_titles:
                    continue
                ln = lines[j]
                if j == i:
                    # Date and title on one line: use the text before the date if it's long enough.
                    before = clean(ln["text"][:dates[0][1]])
                    if len(before) >= 6 and looks_like_title({"text": before}):
                        best = (j, before)
                    continue
                if not looks_like_title(ln):
                    continue
                if find_dates(ln["text"], today):
                    continue
                strong = ln["heading"] or (ln["href"] is not None)
                if strong or dist <= 1:
                    best = (j, ln["text"])
                    break
            if best:
                break
        if not best:
            continue
        ti, title = best
        title = re.sub(r"\s*[|–—-]\s*(sign ?up|register).*$", "", title, flags=re.I).strip()

        # Details from the lines after the date.
        anchor = line["anchor"]
        if anchor is not None and anchor is lines[ti]["anchor"]:
            # Whole card is one link (e.g. Fittmo): the card's own text is the context.
            window = [ln for ln in lines if ln["anchor"] is anchor]
        else:
            window = lines[max(0, min(ti, i) - 2): max(ti, i) + 4]
        ctx = " ".join(ln["text"] for ln in window)
        city = ""
        for ln in lines[i + 1: i + 4]:
            t = ln["text"]
            if (ln["heading"] or GENERIC.match(t) or PRICE_OR_COUNT.match(t) or TIME_RE.match(t)
                    or find_dates(t, today) or len(t) > 90 or "raised" in t.lower()):
                continue
            if ln is lines[ti]:
                break
            city = t
            break
        tm_m = TIME_RE.search(line["text"]) or next(
            (TIME_RE.search(ln["text"]) for ln in lines[i + 1: i + 3] if TIME_RE.search(ln["text"])), None)
        fee_m = next((FEE_RE.search(ln["text"]) for ln in window
                      if FEE_RE.search(ln["text"]) and "raised" not in ln["text"].lower()), None)

        # Link: the title's own link, else a nearby "Sign up / Learn more" link, else the date's link.
        url = lines[ti]["href"]
        if not url:
            for ln in lines[i: i + 7]:
                if ln["href"] and re.match(r"(sign ?up|register|learn more|more info|details|book|get tickets)",
                                           ln["text"], re.I):
                    url = ln["href"]
                    break
        url = url or line["href"] or page_url

        used_titles.add(ti)
        out.append(Candidate(
            name=title, start=start, end=end, src=source["src"], url=clean_url(url), city=city,
            time=tm_m.group(1).upper().replace(".", ":") if tm_m else "",
            fee=fee_m.group(0) if fee_m else "", context=ctx, page_url=page_url))
    return out


def scrape(source: dict, today: dt.date) -> list[Candidate]:
    from bs4 import BeautifulSoup
    html = fetch(source["url"])
    if html is None:
        return []
    soup = BeautifulSoup(html, "html.parser")
    cands = jsonld_candidates(soup, source, source["url"], today)
    if not cands:
        cands = text_candidates(soup, source, source["url"], today)
    keep = []
    for c in cands:
        if not is_running(c, source.get("running_only", False)):
            continue
        last = c.end or c.start
        if last < today or c.start > today + dt.timedelta(days=400):
            continue
        if source.get("org") and not c.org:
            c.org = source["org"]
        keep.append(c)
    # De-duplicate inside one page.
    uniq = {}
    for c in keep:
        uniq.setdefault((c.start, frozenset(name_tokens(c.name))), c)
    return list(uniq.values())


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
PLACEHOLDER = re.compile(r"^(see (event page|sign-up form)|to be announced|tba|venue to be announced)$", re.I)


def find_match(c: Candidate, events: list[dict]):
    best, best_score, how = None, 0.0, ""
    for e in events:
        if "rec" in e or not e.get("start"):
            continue
        es = dt.date.fromisoformat(e["start"])
        days = abs((es - c.start).days)
        sim = similarity(e["name"], c.name)
        same_url = (c.url != clean_url(c.page_url)) and url_key(e.get("url", "")) == url_key(c.url)
        if same_url and days <= 200:
            score, kind = 2.0 + sim, "url"
        elif sim >= 0.6 and days <= 3:
            score, kind = 1.0 + sim, "name+date"
        elif sim >= 0.85 and days <= 120:
            score, kind = sim, "name"
        else:
            continue
        if score > best_score:
            best, best_score, how = e, score, kind
    return best, how


def merge(events: list[dict], cands: list[Candidate], today: dt.date, report: Report) -> None:
    for c in cands:
        e, how = find_match(c, events)
        if e:
            changes = []
            old_start = dt.date.fromisoformat(e["start"])
            if how in ("url", "name") and c.start != old_start and old_start >= today:
                e["start"] = c.start.isoformat()
                changes.append(f"date {old_start.isoformat()} → {c.start.isoformat()}")
                if c.end:
                    e["end"] = c.end.isoformat()
                elif "end" in e and dt.date.fromisoformat(e["end"]) < c.start:
                    del e["end"]
            if c.city and PLACEHOLDER.match(e.get("city", "")):
                e["city"] = c.city
                changes.append(f"venue → {c.city}")
            if c.time and not e.get("time"):
                e["time"] = c.time
                changes.append(f"time → {c.time}")
            if c.fee and not e.get("fee"):
                e["fee"] = c.fee
                changes.append(f"fee → {c.fee}")
            if changes:
                report.changed.append(f"{e['name']}: " + "; ".join(changes) + f" (source: {c.src})")
            continue

        # New event
        blob = f"{c.name} {c.context}"
        base_id = slugify(c.name)
        ids = {x["id"] for x in events}
        new_id = base_id if base_id not in ids else f"{base_id}-{c.start:%Y%m}"
        n = 2
        while new_id in ids:
            new_id = f"{base_id}-{c.start:%Y%m}-{n}"
            n += 1
        ev = {
            "id": new_id,
            "name": c.name,
            "type": guess_type(blob),
            "start": c.start.isoformat(),
        }
        if c.end:
            ev["end"] = c.end.isoformat()
        ev["city"] = c.city or "See event page"
        if c.time:
            ev["time"] = c.time
        ev["dist"] = guess_dist(c.name + " " + c.context) if not c.dist else c.dist
        if c.fee:
            ev["fee"] = c.fee
        ev["org"] = c.org or f"Listed by {c.src}"
        ev["about"] = (f"Listed on {c.src}. Added automatically; check the event page for distances, "
                       f"start time and registration.")
        ev["url"] = c.url
        ev["src"] = c.src
        events.append(ev)
        report.added.append(f"{c.name} – {c.start:%d %b %Y} ({c.src})")


def prune(events: list[dict], today: dt.date, report: Report) -> list[dict]:
    y, m = today.year, today.month - 13
    while m <= 0:
        m += 12
        y -= 1
    try:
        cutoff = today.replace(year=y, month=m)
    except ValueError:
        cutoff = dt.date(y, m, 28)
    keep = []
    for e in events:
        if "rec" in e:
            keep.append(e)
            continue
        last = dt.date.fromisoformat(e.get("end") or e["start"])
        if last < cutoff:
            report.removed.append(f"{e['name']} – {e['start']}")
        else:
            keep.append(e)
    return keep


def sort_events(events: list[dict]) -> list[dict]:
    return sorted(events, key=lambda e: ("1" if "rec" in e else "0", e.get("start", ""), e["name"].lower()))


# ---------------------------------------------------------------------------
# Page build
# ---------------------------------------------------------------------------
def build_page(data: dict) -> bool:
    html = PAGE_FILE.read_text(encoding="utf-8")
    i = html.index(BLOCK_TAG) + len(BLOCK_TAG)
    j = html.index("</script>", i)
    block = "\n" + json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/") + "\n"
    new = html[:i] + block + html[j:]
    if new != html:
        PAGE_FILE.write_text(new, encoding="utf-8")
        return True
    return False


def write_summary(report: Report, total: int, updated: str) -> str:
    lines = [f"## Run calendar refresh – {updated}", "", f"Events tracked: **{total}**", ""]
    for title, items in (("Added", report.added), ("Changed", report.changed), ("Removed", report.removed)):
        lines.append(f"**{title} ({len(items)})**")
        lines += [f"- {x}" for x in items] or ["- none"]
        lines.append("")
    lines.append("**Sources**")
    lines += [f"- {x}" for x in report.source_notes]
    if not (report.added or report.changed or report.removed):
        lines += ["", "No changes found."]
    text = "\n".join(lines)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return text


def main(argv: list[str]) -> int:
    build_only = "--build-only" in argv
    dry_run = "--dry-run" in argv
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    events = data.get("events", [])
    today = today_sl()
    report = Report()

    if not build_only:
        all_cands = []
        for source in SOURCES:
            print(f"Fetching {source['url']}")
            try:
                cands = scrape(source, today)
            except Exception as exc:  # one broken site must never stop the refresh
                print(f"  ! parser error: {type(exc).__name__}: {exc}")
                cands = []
            report.source_notes.append(f"{source['url']}: {len(cands)} upcoming event(s) found")
            for c in cands:
                print(f"  - {c.start} | {c.name} | {c.city} | {c.url}")
            all_cands.extend(cands)
        if dry_run:
            return 0
        merge(events, all_cands, today, report)
        events = prune(events, today, report)
        data["updated"] = dt.datetime.now(SL_TZ).replace(microsecond=0).isoformat()

    data["events"] = sort_events(events)
    if not build_only:
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    build_page(data)
    if not build_only:
        print(write_summary(report, len(data["events"]), data["updated"]))
    else:
        print(f"Built index.html from data/events.json ({len(data['events'])} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
