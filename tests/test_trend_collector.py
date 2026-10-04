"""Tests for trend_collector.py (stdlib-only, no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import trend_collector as tc

TRENDING_HTML = """
<html><body>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/octocat/hello-world" class="Link">octocat / hello-world</a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4"> A free trending fixture repo </p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/octocat/hello-world/stargazers" class="Link">1,234</a>
  <span class="d-inline-block float-sm-right"> 567 stars today </span>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/alice/tools">alice / tools</a>
  </h2>
  <p>Desc without language</p>
  <a href="/alice/tools/stargazers">99</a>
  <span>12 stars today</span>
</article>
</body></html>
"""


class ParserTest(unittest.TestCase):
    def test_parse_trending(self) -> None:
        records = tc.parse_trending(TRENDING_HTML)
        self.assertEqual(len(records), 2)
        first = records[0]
        self.assertEqual(first["url"], "https://github.com/octocat/hello-world")
        self.assertEqual(first["owner"], "octocat")
        self.assertEqual(first["repo"], "hello-world")
        self.assertEqual(first["language"], "Python")
        self.assertEqual(first["stars"], 1234)
        self.assertEqual(first["stars_today"], 567)
        self.assertEqual(first["description"], "A free trending fixture repo")

    def test_parse_tolerates_missing_fields(self) -> None:
        records = tc.parse_trending(TRENDING_HTML)
        second = records[1]
        self.assertEqual(second["stars"], 99)
        self.assertIsNone(second["language"])
        self.assertEqual(second["stars_today"], 12)

    def test_parse_empty_page(self) -> None:
        self.assertEqual(tc.parse_trending("<html><body><p>empty</p></body></html>"), [])


class SpikeTest(unittest.TestCase):
    @staticmethod
    def _history(series: dict[str, dict[str, int]]) -> dict:
        return {"https://github.com/a/b": {"name": "a/b", "first_seen": "d1", "series": series}}

    def test_spike_detected(self) -> None:
        history = self._history(
            {
                "2026-10-01": {"stars": 100},
                "2026-10-02": {"stars": 105},
                "2026-10-03": {"stars": 110},
                "2026-10-04": {"stars": 910},
            }
        )
        report = tc.analyze(history)
        self.assertEqual(len(report["spikes"]), 1)
        self.assertEqual(report["spikes"][0]["delta"], 800)
        self.assertEqual(report["top_movers"][0]["url"], "https://github.com/a/b")

    def test_flat_series_no_spike(self) -> None:
        history = self._history(
            {
                "2026-10-01": {"stars": 100},
                "2026-10-02": {"stars": 104},
                "2026-10-03": {"stars": 108},
                "2026-10-04": {"stars": 110},
            }
        )
        report = tc.analyze(history)
        self.assertEqual(report["spikes"], [])

    def test_single_day_history_needs_more(self) -> None:
        history = self._history({"2026-10-01": {"stars": 100}})
        report = tc.analyze(history)
        self.assertEqual(report["spikes"], [])
        self.assertEqual(report["top_movers"], [])
        self.assertEqual(report["repos_tracked"], 1)


class SnapshotTest(unittest.TestCase):
    def test_write_snapshot_merges_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            record = {"url": "https://github.com/a/b", "owner": "a", "repo": "b", "stars": 100, "stars_today": 5}
            tc.write_snapshot(data_dir, [record], [], [])
            tc.write_snapshot(data_dir, [dict(record, stars=110)], [], [])
            history = tc._load_json(data_dir / "history.json", {})
            days = history["https://github.com/a/b"]["series"]
            self.assertEqual(len(days), 1)  # same calendar day overwrites, not duplicates
            self.assertEqual(next(iter(days.values()))["stars"], 110)
            self.assertTrue((data_dir / "snapshots").exists())


if __name__ == "__main__":
    unittest.main()
