#!/usr/bin/env python3
"""
TCA Member News Monitor
------------------------
Checks Google News for ANY mention of Tech Council of Australia members
(positive or negative), keeps only stories published by Australian news
sources, dedupes against previously-seen stories, and produces:

  1. output/feed.xml   - an RSS feed you can subscribe to in any feed reader
  2. output/digest.html - a ready-to-send HTML email digest of NEW items only

Optionally sends the digest by email via SMTP if the relevant environment
variables are set (see README.md).

Run it once a day (via cron or GitHub Actions - see README.md).
"""

import json
import os
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
from feedgen.feed import FeedGenerator

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
MEMBERS_FILE = BASE_DIR / "members.json"
DOMAINS_FILE = BASE_DIR / "domains.json"
STATE_FILE = BASE_DIR / "state.json"
OUTPUT_DIR = BASE_DIR / "output"

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "30"))  # slightly > 24h for safety margin
MAX_SEEN_LINKS = 5000  # cap state file size
REQUEST_DELAY_SECONDS = 1.0  # be polite to Google News between requests
GOOGLE_NEWS_LOCALE = "hl=en-AU&gl=AU&ceid=AU:en"

FEED_TITLE = "TCA Member News"
FEED_LINK = "https://techcouncil.com.au/members/"
FEED_DESC = "Automated feed of Australian news mentions of Tech Council of Australia members."


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if STATE_FILE.exists():
        return load_json(STATE_FILE)
    return {"seen_links": []}


def save_state(state):
    # cap size so the file doesn't grow forever
    state["seen_links"] = state["seen_links"][-MAX_SEEN_LINKS:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def build_query(member):
    # Exact match on the member name. No keyword filtering - we want ANY
    # mention, positive or negative. Australian-only filtering happens
    # after fetching, based on the publisher's actual domain.
    return f'"{member}"'


def fetch_google_news_rss(query, retries=3):
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&{GOOGLE_NEWS_LOCALE}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            return feedparser.parse(data)
        except Exception as e:
            if attempt == retries - 1:
                print(f"  ! Failed to fetch for query [{query}]: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (attempt + 1))
    return None


def entry_is_recent(entry, cutoff):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return True  # if we can't tell, don't discard it
    entry_dt = datetime.fromtimestamp(time.mktime(published), tz=timezone.utc)
    return entry_dt >= cutoff


def get_source_domain(entry):
    """
    Google News RSS items include a <source url="..."> tag pointing at the
    publisher's own site - this is far more reliable than the <link>, which
    is often a news.google.com redirect URL rather than the real article URL.
    Falls back to the link's domain if no source tag is present.
    """
    source = entry.get("source")
    href = source.get("href") if source else None
    if not href:
        href = entry.get("link", "")
    netloc = urllib.parse.urlparse(href).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def entry_is_australian(entry, extra_domains):
    domain = get_source_domain(entry)
    if not domain:
        return False
    if domain.endswith(".au"):
        return True
    return any(domain == d or domain.endswith("." + d) for d in extra_domains)


def collect_new_stories(members, extra_domains, state):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    seen = set(state["seen_links"])
    new_items = []

    for i, member in enumerate(members, 1):
        query = build_query(member)
        print(f"[{i}/{len(members)}] Checking: {member}")
        feed = fetch_google_news_rss(query)
        time.sleep(REQUEST_DELAY_SECONDS)
        if not feed or not feed.entries:
            continue

        for entry in feed.entries:
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            if not entry_is_recent(entry, cutoff):
                continue
            if not entry_is_australian(entry, extra_domains):
                continue

            source_info = entry.get("source")
            new_items.append({
                "member": member,
                "title": entry.get("title", "(no title)"),
                "link": link,
                "published": entry.get("published", ""),
                "source": source_info.get("title", "") if source_info else get_source_domain(entry),
                "summary": entry.get("summary", ""),
            })
            seen.add(link)

    state["seen_links"] = list(seen)
    return new_items, state


def get_combined_items(new_items, existing_feed_path, max_items=200):
    """
    Merges today's new items with whatever was already in the feed file,
    so both the RSS feed and the public webpage show a rolling history,
    not just the latest batch. Used by write_rss_feed and write_public_page.
    """
    combined = list(new_items)

    if existing_feed_path.exists():
        old_feed = feedparser.parse(str(existing_feed_path))
        existing_links = {item["link"] for item in new_items}
        for entry in old_feed.entries:
            if entry.link not in existing_links:
                combined.append({
                    "member": entry.get("author", ""),
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "source": "",
                    "summary": entry.get("summary", ""),
                })

    return combined[:max_items]


def write_rss_feed(combined_items, existing_feed_path):
    """
    Builds output/feed.xml from the already-merged rolling list of items.
    """
    fg = FeedGenerator()
    fg.title(FEED_TITLE)
    fg.link(href=FEED_LINK, rel="alternate")
    fg.description(FEED_DESC)
    fg.language("en-au")

    for item in combined_items:
        fe = fg.add_entry()
        fe.title(f"[{item['member']}] {item['title']}")
        fe.link(href=item["link"])
        fe.description(item.get("summary", "") or item["title"])
        fe.author(name=item["member"])

    OUTPUT_DIR.mkdir(exist_ok=True)
    fg.rss_file(str(existing_feed_path))


def write_public_page(combined_items, path):
    """
    Builds a plain webpage (index.html, at the repo root) listing the
    rolling history of mentions - no RSS reader or login needed. Meant to
    be served for free via GitHub Pages.
    """
    if not combined_items:
        rows_html = "<p>No mentions found yet.</p>"
    else:
        rows_html = "\n".join(
            f"""
            <tr>
              <td style="padding:10px;border-bottom:1px solid #eee;font-weight:bold;white-space:nowrap;">{item['member']}</td>
              <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{item['link']}" target="_blank" rel="noopener">{item['title']}</a><br>
                <span style="color:#888;font-size:12px;">{item['source']} — {item['published']}</span>
              </td>
            </tr>"""
            for item in combined_items
        )

    html = f"""<!DOCTYPE html>
<html lang="en-au">
<head>
<meta charset="utf-8">
<title>TCA Member News</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="font-family: -apple-system, Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #222;">
  <h1>TCA Member News</h1>
  <p style="color:#666;">Australian news mentions of Tech Council of Australia members.
  Last updated: {datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}.
  Showing the most recent {len(combined_items)} item(s).</p>
  <table style="width:100%;border-collapse:collapse;">
    {rows_html}
  </table>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_html_digest(new_items, path):
    if not new_items:
        html = "<html><body><p>No new member mentions found today.</p></body></html>"
    else:
        rows = "\n".join(
            f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">{item['member']}</td>
              <td style="padding:8px;border-bottom:1px solid #eee;">
                <a href="{item['link']}">{item['title']}</a><br>
                <span style="color:#888;font-size:12px;">{item['source']} — {item['published']}</span>
              </td>
            </tr>"""
            for item in new_items
        )
        html = f"""
        <html><body>
        <h2>TCA Member News — {datetime.now().strftime('%d %b %Y')}</h2>
        <p>{len(new_items)} new mention(s) found in the last {LOOKBACK_HOURS} hours (Australian sources only):</p>
        <table style="width:100%;border-collapse:collapse;">{rows}</table>
        </body></html>
        """
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return html


def send_email(html_body, new_items):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    email_to = os.environ.get("EMAIL_TO")
    email_from = os.environ.get("EMAIL_FROM", smtp_user)

    if not all([smtp_host, smtp_port, smtp_user, smtp_pass, email_to]):
        print("SMTP env vars not fully set — skipping email send (RSS/HTML files were still written).")
        return

    if not new_items:
        print("No new items — skipping email send.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"TCA Member News — {len(new_items)} new mention(s)"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(email_from, [email_to], msg.as_string())
    print(f"Email sent to {email_to}")


def main():
    members = load_json(MEMBERS_FILE)
    domains_cfg = load_json(DOMAINS_FILE)
    extra_domains = [d.lower() for d in domains_cfg.get("extra_domains", [])]
    state = load_state()

    new_items, state = collect_new_stories(members, extra_domains, state)
    save_state(state)

    OUTPUT_DIR.mkdir(exist_ok=True)
    feed_path = OUTPUT_DIR / "feed.xml"
    combined_items = get_combined_items(new_items, feed_path)

    write_rss_feed(combined_items, feed_path)
    write_public_page(combined_items, BASE_DIR / "index.html")
    html_body = write_html_digest(new_items, OUTPUT_DIR / "digest.html")
    send_email(html_body, new_items)

    print(f"\nDone. {len(new_items)} new item(s) found.")
    for item in new_items:
        print(f"  - [{item['member']}] {item['title']} ({item['source']})")


if __name__ == "__main__":
    main()
