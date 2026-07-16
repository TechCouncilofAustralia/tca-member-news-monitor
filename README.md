# TCA Member News Monitor

Watches for "good news" mentions (awards, grants, funding, recognition, etc.)
of Tech Council of Australia members, so you can spot LinkedIn post
opportunities.

## How it works

1. `members.json` — the list of ~170 TCA members, pulled from
   https://techcouncil.com.au/members/. Edit freely (add/remove companies).
2. `keywords.json` — words that make a story count as "good news"
   (`good_news_keywords`) and words that filter a story OUT even if a
   keyword matches (`exclude_keywords`, e.g. "lawsuit", "layoffs").
3. `monitor.py` — for each member, queries Google News RSS with the member
   name + an OR-group of the good-news keywords + "Australia" (this cuts
   down noise a lot for global brands like Google, Apple, Microsoft, IBM,
   who are also TCA members and would otherwise flood you with irrelevant
   global stories).
4. Results are deduped against `state.json` (a running list of links
   already seen) so you never get the same story twice.
5. Output:
   - `output/feed.xml` — a rolling RSS feed of the last ~200 matched
     stories. Subscribe to this in Feedly/Inoreader/Outlook/etc.
   - `output/digest.html` — an HTML digest of only *today's new* items.
   - If SMTP details are set as environment variables, it also emails you
     the digest directly (skipped automatically if no new items).

## Option A — Run it yourself, once a day (cron)

```bash
pip install -r requirements.txt
python monitor.py
```

Add to your crontab (`crontab -e`) to run every morning at 7am:

```
0 7 * * * cd /path/to/tca-news-monitor && /usr/bin/python3 monitor.py >> run.log 2>&1
```

To get emailed, export these before running (or put them in a `.env` you
source from cron):

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASS=your_app_password   # not your normal password - use an app password
export EMAIL_TO=you@yourcompany.com
```

(Gmail, Outlook, and most providers require an "app password" for SMTP,
not your login password — search "[your provider] app password" if you're
not sure how to generate one.)

## Option B — Run it for free on GitHub Actions (recommended — no server needed)

1. Create a **private** GitHub repo and push this folder to it.
2. In the repo, go to **Settings → Secrets and variables → Actions** and
   add (only needed if you want email; skip if you're just using the RSS
   feed):
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`,
     `EMAIL_FROM`
3. The workflow in `.github/workflows/daily-check.yml` runs automatically
   every day at 21:00 UTC (adjust the cron line for your preferred time)
   and commits the updated `feed.xml`/`digest.html`/`state.json` back to
   the repo. You can also trigger it manually from the **Actions** tab
   any time ("Run workflow").
4. To subscribe to the RSS feed from a repo, turn on **GitHub Pages**
   (Settings → Pages → serve from the `output` folder) — you'll get a URL
   like `https://yourusername.github.io/yourrepo/feed.xml` that any feed
   reader can subscribe to. Alternatively, use the raw GitHub URL:
   `https://raw.githubusercontent.com/yourusername/yourrepo/main/output/feed.xml`
   (works in most feed readers without needing Pages at all).

This costs nothing — GitHub Actions gives free minutes for private repos,
and this job runs in well under a minute a day.

## Tuning it

- **Too much noise?** Trim `good_news_keywords` in `keywords.json`, or add
  more terms to `exclude_keywords`. You can also remove very "noisy" big
  global brands from `members.json` (Google, Apple, Microsoft, IBM, Adobe,
  etc.) if their general news volume overwhelms the Australia + keyword
  filter — those companies rarely need TCA to know about their news
  anyway.
- **Missing stories?** Increase `LOOKBACK_HOURS` (default 30) if you don't
  run it daily, or broaden `good_news_keywords`.
- **Want it to check for full company milestones like "Series B" only?**
  Just trim `good_news_keywords` down to the specific terms you care
  about — the query is literally `"CompanyName" (kw1 OR kw2 OR ...)
  Australia`, so fewer keywords = tighter, more relevant matches.

## Notes / limitations

- This uses Google News' public RSS search, which is free and needs no
  API key, but is an unofficial interface — Google could change its
  format. If it stops returning results, that's the first thing to check.
- 170 members × 1 request each with a polite 1s delay = ~3 minutes per
  run, which fits comfortably in a GitHub Actions job.
- It's a *filter*, not a guarantee — a genuinely great story that doesn't
  use any of your keywords could slip through, and a keyword can match
  something irrelevant. Treat the digest as a shortlist to skim, not a
  fully-automated pipeline straight to LinkedIn.
