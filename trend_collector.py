"""Free GitHub trend collector: daily trending snapshot + star-spike report.

Sources (all free, no paid API):
- github.com/trending scrape — GitHub's own daily order, no auth
- GitHub Search API — repos created in the last N days sorted by stars
  (optional GITHUB_TOKEN, falls back to `gh auth token`, then unauthenticated)

Usage:
    python trend_collector.py snapshot          # fetch both sources -> data/trends
    python trend_collector.py report            # spikes + top movers from history
    python trend_collector.py all               # snapshot then report

Data lives under --data-dir (default data/trends/):
    snapshots/YYYY-MM-DD.json   raw fetch of the day
    history.json                per-repo star/fork series across days
    report.json                 latest spike/mover analysis
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

UA = "relevio-trend-watcher/1.0"
TRENDING_URL = "https://github.com/trending?since=daily"
_SPIKE_MIN_ABS = 10
_SPIKE_FACTOR = 4.0


def _http_get(url: str, token: str | None = None, timeout: int = 30) -> bytes:
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class TrendingParser(HTMLParser):
    """Extract repo records from the github.com/trending HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []
        self._cur: dict | None = None
        self._target: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "article" and self._cur is None:
            self._cur = {
                "url": None,
                "owner": None,
                "repo": None,
                "description": None,
                "language": None,
                "stars": None,
                "stars_today": None,
            }
            return
        if self._cur is None:
            return
        if tag == "h2":
            self._target = "heading"
        elif tag == "p" and self._cur["description"] is None:
            self._target = "description"
        elif tag == "span" and attributes.get("itemprop") == "programmingLanguage":
            self._target = "language"
        elif tag == "a" and (attributes.get("href") or "").endswith("/stargazers"):
            self._target = "stars"
            href = attributes["href"] or ""
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) >= 2:
                self._cur["owner"], self._cur["repo"] = parts[0], parts[1]
                self._cur["url"] = f"https://github.com/{parts[0]}/{parts[1]}"

    def handle_data(self, data: str) -> None:
        if self._cur is None:
            return
        self._text.append(data)
        if self._target == "heading" and not self._cur["url"]:
            match = re.match(r"\s*(\S+)\s*/\s*(\S+)", data)
            if match:
                self._cur["owner"], self._cur["repo"] = match.group(1), match.group(2)
                self._cur["url"] = f"https://github.com/{match.group(1)}/{match.group(2)}"
        elif self._target == "description":
            self._cur["description"] = (self._cur["description"] or "") + data
        elif self._target == "language":
            self._cur["language"] = data.strip()
        elif self._target == "stars":
            self._cur["stars"] = _num(data) if self._cur["stars"] is None else self._cur["stars"]

    def handle_endtag(self, tag: str) -> None:
        if self._cur is None:
            return
        if tag in {"h2", "p", "span", "a"}:
            self._target = None
        if tag == "article":
            blob = "".join(self._text)
            match = re.search(r"([\d,\.]+)\s+stars\s+today", blob)
            if match:
                self._cur["stars_today"] = _num(match.group(1))
            if self._cur["url"]:
                if self._cur["description"]:
                    self._cur["description"] = self._cur["description"].strip() or None
                self.records.append(self._cur)
            self._cur = None
            self._target = None
            self._text = []


def parse_trending(html: str) -> list[dict]:
    parser = TrendingParser()
    parser.feed(html)
    return parser.records


def fetch_trending(token: str | None = None, url: str = TRENDING_URL) -> list[dict]:
    html = _http_get(url, token=token).decode("utf-8", errors="replace")
    return parse_trending(html)


def fetch_rising(days: int = 7, token: str | None = None, per_page: int = 30) -> list[dict]:
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    query = urllib.parse.urlencode({"q": f"created:>{since}", "sort": "stars", "order": "desc", "per_page": per_page})
    data = json.loads(_http_get(f"https://api.github.com/search/repositories?{query}", token=token))
    results = []
    for item in data.get("items", []):
        results.append(
            {
                "url": item.get("html_url"),
                "owner": (item.get("owner") or {}).get("login"),
                "repo": item.get("name"),
                "description": item.get("description"),
                "language": item.get("language"),
                "stars": item.get("stargazers_count"),
                "stars_today": None,
                "forks": item.get("forks_count"),
                "created_at": item.get("created_at"),
            }
        )
    return results


def _token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:  # best effort: reuse the authenticated gh CLI session, never print it
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False)  # noqa: S607 - gh resolved via PATH
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _load_json(path: Path, default: object) -> object:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def write_snapshot(data_dir: Path, trending: list[dict], rising: list[dict], errors: list[str]) -> dict:
    day = datetime.now(timezone.utc).date().isoformat()
    snapshots = data_dir / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "date": day,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "github_trending": trending,
        "rising_7d": rising,
        "errors": errors,
    }
    (snapshots / f"{day}.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    history = _load_json(data_dir / "history.json", {})
    for source, records in (("github_trending", trending), ("rising_7d", rising)):
        for record in records:
            url = record.get("url")
            if not url or record.get("stars") is None:
                continue
            entry = history.setdefault(
                url, {"name": f"{record.get('owner')}/{record.get('repo')}", "first_seen": day, "series": {}}
            )
            entry["series"][day] = {"stars": record["stars"], "forks": record.get("forks"), "source": source}
    (data_dir / "history.json").write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot


def analyze(history: dict[str, dict]) -> dict:
    spikes, movers = [], []
    for url, entry in history.items():
        points = sorted(
            (day, values["stars"]) for day, values in entry.get("series", {}).items() if values.get("stars") is not None
        )
        if len(points) < 2:
            continue
        deltas = [points[i][1] - points[i - 1][1] for i in range(1, len(points))]
        latest, prior = deltas[-1], deltas[:-1]
        baseline = statistics.median(prior) if prior else 0.0
        row = {
            "url": url,
            "name": entry.get("name"),
            "stars": points[-1][1],
            "delta": latest,
            "days": len(points),
            "baseline_median": round(baseline, 1),
        }
        movers.append(row)
        if latest >= max(_SPIKE_MIN_ABS, _SPIKE_FACTOR * max(baseline, 0.0)):
            spikes.append(row)
    spikes.sort(key=lambda row: row["delta"], reverse=True)
    movers.sort(key=lambda row: row["delta"], reverse=True)
    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "repos_tracked": len(history),
        "spikes": spikes,
        "top_movers": movers[:20],
        "rule": f"delta >= max({_SPIKE_MIN_ABS}, {_SPIKE_FACTOR}x median of prior deltas)",
    }


def print_report(report: dict) -> None:
    print(f"\n== star spikes ({report['date']}) — rule: {report['rule']}")
    if report["spikes"]:
        for row in report["spikes"]:
            print(
                f"  SPIKE {row['name']}: +{row['delta']} stars (now {row['stars']}, baseline {row['baseline_median']})"
            )
    else:
        print("  no spikes yet (need >= 2 snapshot days)")
    print(f"\n== top movers, last day ({report['repos_tracked']} repos tracked)")
    for row in report["top_movers"][:10]:
        print(f"  {row['delta']:>6}  {row['name']}  ({row['stars']} stars, {row['days']}d)")
    if not report["top_movers"]:
        print("  no history yet — run `snapshot` again tomorrow for deltas")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["snapshot", "report", "all"], nargs="?", default="all")
    parser.add_argument("--data-dir", default="data/trends")
    parser.add_argument("--days", type=int, default=7, help="look-back window for rising repos")
    parser.add_argument("--per-page", type=int, default=30, help="rising repos to fetch")
    parser.add_argument("--trending-url", default=TRENDING_URL)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    if args.command in {"snapshot", "all"}:
        token = _token()
        trending: list[dict] = []
        rising: list[dict] = []
        try:
            trending = fetch_trending(token=token, url=args.trending_url)
        except (urllib.error.URLError, OSError) as exc:
            errors.append(f"trending scrape failed: {exc}")
            print(f"[warn] trending scrape failed: {exc}", file=sys.stderr)
        try:
            rising = fetch_rising(days=args.days, token=token, per_page=args.per_page)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"search api failed: {exc}")
            print(f"[warn] search api failed: {exc}", file=sys.stderr)
        snapshot = write_snapshot(data_dir, trending, rising, errors)
        print(f"[snapshot] {snapshot['date']}: {len(trending)} trending, {len(rising)} rising -> {data_dir}/snapshots/")

    if args.command in {"report", "all"}:
        history = _load_json(data_dir / "history.json", {})
        report = analyze(history)
        (data_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print_report(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
