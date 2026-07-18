# knowitall

Personal one-stop news brief. No Inshorts, no channel front pages — raw headlines,
straight from the sources, and the page makes you go through **all of them** before
it lets you browse.

Live: https://vaibhavgit9210.github.io/knowitall/

## How it works

- **`scripts/fetch_news.py`** (Python stdlib, zero deps) pulls RSS from:
  - *Direct sections* (India / World / Business / Sports / Tech): Google News
    topic + keyword-search feeds (headlines are the publisher's own, ranked
    algorithmically) plus wire/official sources — PIB, Reuters, AP, PTI via
    `site:` searches.
  - *News Channels section*: top-stories RSS of TOI, NDTV, The Hindu, HT,
    India Today, BBC, Al Jazeera.
  - Dedupes near-identical stories (token overlap), drops items older than 36h,
    caps 30/section, filters out non-English headlines.
  - **Importance ranking**: each story gets a `hot` score = number of distinct
    outlets running a similar headline anywhere in the fetch. Sections are
    sorted hot-first, so trending stories (the ones everyone is covering) lead
    the brief; the UI shows a 🔥 badge with the outlet count.
- **`.github/workflows/update-news.yml`** runs the script every 30 minutes,
  commits `data/news.json` + daily logs to `main`, and force-syncs `gh-pages`
  so the site updates itself. No servers, no tokens, no cost.
- **`index.html`** — the whole site. Three views:
  - **Brief** (default): one headline at a time, hottest first. Space/→ to
    advance. No list, no skipping. Progress bar tracks you. Category chips
    (All / India / World / Business / Sports / Tech / Channels) narrow the
    brief to one section — also deep-linkable as `?sec=tech`.
  - **Browse**: full grouped list — 🔒 locked until you finish the brief.
  - **Archive**: every headline that ever appeared on the page, one JSON per
    IST day in `logs/`, browsable by date. Git history is a second audit log.
- Read-state lives in `localStorage` (per browser), pruned after 10 days.

## No secrets needed

Everything runs on public RSS + GitHub Actions + GitHub Pages. There is
nothing to configure and no API key to add.

## Tweaking

- Add/remove feeds: edit the `FEEDS` dict in `scripts/fetch_news.py`.
- Volume: `PER_FEED_CAP` / `PER_SECTION_CAP` / `MAX_AGE` at the top of the script.
- Cadence: the cron line in the workflow.

Note: GitHub pauses scheduled workflows after ~60 days without repo activity;
the bot's own commits normally keep it alive, but if headlines ever go stale,
hit "Run workflow" once in the Actions tab to revive it.
