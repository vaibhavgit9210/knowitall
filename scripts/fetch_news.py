#!/usr/bin/env python3
"""Fetch headlines from RSS feeds into data/news.json + daily logs.

Stdlib only — runs in GitHub Actions with no pip installs.
Two kinds of sections:
  - "direct" sections (india/world/business/sports/tech): Google News topic &
    keyword-search feeds + official/wire sources. Headlines come from the
    source publisher, ranked algorithmically — no single channel's editorial cut.
  - "channels": top-stories RSS of mainstream news channels, one combined section.
"""

import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "news.json"
LOG_DIR = ROOT / "logs"

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime.now(timezone.utc)
MAX_AGE = timedelta(hours=36)   # drop items older than this
PER_FEED_CAP = 12               # max items taken from a single feed
PER_SECTION_CAP = 30            # max items shown per section

GN = "hl=en-IN&gl=IN&ceid=IN:en"

FEEDS = {
    "india": [
        ("Google News · India", f"https://news.google.com/rss/headlines/section/topic/NATION?{GN}"),
        ("PIB (Govt of India)", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"),
        ("Wires · India", f"https://news.google.com/rss/search?q=india%20(site:reuters.com%20OR%20site:apnews.com%20OR%20site:ptinews.com)%20when:1d&{GN}"),
    ],
    "world": [
        ("Google News · World", f"https://news.google.com/rss/headlines/section/topic/WORLD?{GN}"),
        ("Wires · World", f"https://news.google.com/rss/search?q=(site:reuters.com%20OR%20site:apnews.com)%20when:1d&{GN}"),
    ],
    "business": [
        ("Google News · Business", f"https://news.google.com/rss/headlines/section/topic/BUSINESS?{GN}"),
        ("Wires · Business", f"https://news.google.com/rss/search?q=business%20(site:reuters.com%20OR%20site:apnews.com)%20when:1d&{GN}"),
    ],
    "sports": [
        ("Google News · Sports", f"https://news.google.com/rss/headlines/section/topic/SPORTS?{GN}"),
    ],
    "tech": [
        ("Google News · Tech", f"https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?{GN}"),
        ("Wires · Tech", f"https://news.google.com/rss/search?q=technology%20(site:reuters.com%20OR%20site:apnews.com)%20when:1d&{GN}"),
    ],
    "channels": [
        ("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
        ("NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
        ("The Hindu", "https://www.thehindu.com/news/feeder/default.rss"),
        ("Hindustan Times", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
        ("India Today", "https://www.indiatoday.in/rss/1206578"),
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ],
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) knowitall-personal-news-brief"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def text(el, tag):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse_date(raw):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_feed(xml_bytes):
    """Yield (title, link, published_dt) from RSS 2.0 or Atom."""
    root = ElementTree.fromstring(xml_bytes)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.iter("item"):  # RSS 2.0
        title = text(item, "title")
        link = text(item, "link")
        when = parse_date(text(item, "pubDate") or text(item, "{http://purl.org/dc/elements/1.1/}date"))
        if title and link:
            yield title, link, when
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):  # Atom
        title = text(entry, "atom:title".replace("atom:", "{http://www.w3.org/2005/Atom}"))
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        when = parse_date(text(entry, "{http://www.w3.org/2005/Atom}updated"))
        if title and link:
            yield title, link, when


def norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


STOPWORDS = set("""a an and are as at be but by for from has have he her his how i in is it its
of on or say says she that the their there they this to was were what when who will with you your
after over amid new live updates news top today latest breaking""".split())


def sig_tokens(title):
    return {w for w in norm_title(title).split() if len(w) >= 3 and w not in STOPWORDS}


def similar(a, b):
    """Do two headline token-sets look like the same story?"""
    if len(a) < 2 or len(b) < 2:
        return False
    shared = len(a & b)
    return shared >= 2 and shared / min(len(a), len(b)) >= 0.5


def mostly_english(title):
    ascii_letters = sum(c.isascii() for c in title)
    return ascii_letters / max(len(title), 1) >= 0.7


def split_google_title(title):
    """Google News titles end with ' - Publisher'. Split it out."""
    parts = title.rsplit(" - ", 1)
    if len(parts) == 2 and 0 < len(parts[1]) <= 40:
        return parts[0].strip(), parts[1].strip()
    return title, ""


def collect():
    """Two passes: gather every raw entry (the scoring corpus), then per section
    dedupe near-identical stories and score each by how many distinct outlets
    are running a similar headline anywhere in the corpus ("hot")."""
    raw = {s: [] for s in FEEDS}   # section -> raw entries
    corpus = []                    # (tokens, source) for every entry everywhere
    failures = []
    for section, feeds in FEEDS.items():
        for feed_name, url in feeds:
            try:
                entries = list(parse_feed(fetch(url)))
            except Exception as exc:  # noqa: BLE001 - a dead feed must not kill the run
                failures.append(f"{feed_name}: {exc}")
                continue
            count = 0
            for title, link, when in entries:
                if count >= PER_FEED_CAP:
                    break
                if when and NOW - when > MAX_AGE:
                    continue
                source = feed_name
                if "news.google.com" in url:
                    title, publisher = split_google_title(title)
                    if publisher:
                        source = publisher
                if not norm_title(title) or not mostly_english(title):
                    continue
                entry = {"title": title, "link": link, "when": when, "source": source,
                         "tokens": sig_tokens(title)}
                raw[section].append(entry)
                corpus.append((entry["tokens"], source))
                count += 1

    sections = {}
    for section, entries in raw.items():
        items, seen_keys, kept_tokens = [], set(), []
        for e in entries:
            key = norm_title(e["title"])
            if key in seen_keys or any(similar(e["tokens"], t) for t in kept_tokens):
                continue  # same story already kept in this section
            seen_keys.add(key)
            kept_tokens.append(e["tokens"])
            hot = len({src for toks, src in corpus if similar(e["tokens"], toks)})
            items.append({
                "id": hashlib.sha1(key.encode()).hexdigest()[:12],
                "title": e["title"],
                "link": e["link"],
                "source": e["source"],
                "hot": hot,   # distinct outlets running this story (1 = only here)
                "publishedAt": e["when"].astimezone(timezone.utc).isoformat(timespec="seconds") if e["when"] else None,
            })
        items.sort(key=lambda i: i["publishedAt"] or "", reverse=True)
        items.sort(key=lambda i: i["hot"], reverse=True)  # stable: hot first, fresh first within same hot
        sections[section] = items[:PER_SECTION_CAP]
    return sections, failures


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def main():
    sections, failures = collect()
    for line in failures:
        print(f"feed failed: {line}", file=sys.stderr)

    total = sum(len(v) for v in sections.values())
    if total == 0:
        print("no items fetched at all — keeping previous data", file=sys.stderr)
        sys.exit(1)

    # preserve firstSeen across runs
    prev = load_json(DATA_FILE, {})
    prev_seen = {i["id"]: i.get("firstSeen") for s in prev.get("sections", {}).values() for i in s}
    now_iso = NOW.isoformat(timespec="seconds")
    for items in sections.values():
        for item in items:
            item["firstSeen"] = prev_seen.get(item["id"]) or now_iso

    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps({
        "updatedAt": now_iso,
        "sections": sections,
    }, ensure_ascii=False, indent=1))

    # daily log (IST date): every headline that appeared on the page that day
    LOG_DIR.mkdir(exist_ok=True)
    day = NOW.astimezone(IST).strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{day}.json"
    log = load_json(log_file, {"date": day, "items": []})
    logged = {i["id"] for i in log["items"]}
    added = 0
    for section, items in sections.items():
        for item in items:
            if item["id"] not in logged:
                log["items"].append({**item, "section": section})
                added += 1
    log_file.write_text(json.dumps(log, ensure_ascii=False, indent=1))

    index_file = LOG_DIR / "index.json"
    days = sorted((p.stem for p in LOG_DIR.glob("????-??-??.json")), reverse=True)
    index_file.write_text(json.dumps(days))

    print(f"ok: {total} items across {len(sections)} sections, {added} new logged for {day}")


if __name__ == "__main__":
    main()
