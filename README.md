# GitHub Trend Watcher

Free, self-hosted alternative to paid trend-signal APIs (TrendShift Signal, etc.):
daily GitHub trending snapshots, a rising-repos feed, and star-spike detection —
built only on free/public sources. Single file, Python 3.12, **stdlib only**.

## Free sources (no paid API)

| Feature | Source | Auth |
|---|---|---|
| Daily trending list (GitHub's own order) | scrape of `github.com/trending?since=daily` | none |
| Rising repos (created last 7 days, by stars) | GitHub Search API `/search/repositories` | optional (`GITHUB_TOKEN`, else `gh auth token`, else anonymous) |
| Star-spike detection + top movers | computed from your own daily history | none |

Spike rule: last day-over-day star delta ≥ `max(10, 4 × median of prior deltas)`.
With one day of history there are no deltas yet — run `snapshot` daily and the
report gains power each day.

## Usage

```bash
python trend_collector.py snapshot    # fetch -> data/trends/snapshots/YYYY-MM-DD.json + history.json
python trend_collector.py report      # spikes + top movers -> data/trends/report.json
python trend_collector.py all         # both
```

Options: `--data-dir` (default `data/trends`), `--days` (rising window, default 7),
`--per-page` (default 30), `--trending-url`.

Outputs:

| File | Content |
|---|---|
| `data/trends/snapshots/YYYY-MM-DD.json` | raw fetch: `github_trending` + `rising_7d` |
| `data/trends/history.json` | per-repo star/fork series across days |
| `data/trends/report.json` | latest spike/mover analysis |

## Development

```bash
ruff check .                       # pinned rules in ruff.toml
python -m compileall -q .
python -m unittest discover -s tests -v   # 7 tests, stdlib, no network
```

## Notes

- The token is only sent as an auth header and never printed or logged.
- `data/` (your collected history) is gitignored.
- TrendShift's proprietary repo ranking is the one thing this does not
  replicate; their website remains free to browse.
