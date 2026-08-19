import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / ".claude/skills/run/scripts/feedstate.py"
SPEC = importlib.util.spec_from_file_location("feedstate", SCRIPT)
feedstate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feedstate)


class FeedStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_state = os.environ.get("GITHUB_TRENDING_STATE")
        os.environ["GITHUB_TRENDING_STATE"] = self.temporary.name

    def tearDown(self):
        if self.previous_state is None:
            os.environ.pop("GITHUB_TRENDING_STATE", None)
        else:
            os.environ["GITHUB_TRENDING_STATE"] = self.previous_state
        self.temporary.cleanup()

    def test_repository_is_seen_across_configuration_scopes(self):
        daily = "https://github.com/trending/python?since=daily"
        monthly = "https://github.com/trending/rust?since=monthly"

        feedstate.mark_seen(daily, ["Owner/Repository"])

        self.assertEqual(
            feedstate.filter_seen(monthly, ["owner/repository", "other/project"]),
            ["other/project"],
        )
        self.assertEqual(feedstate.state_paths(daily), feedstate.state_paths(monthly))

        index_path = Path(self.temporary.name) / "index.json"
        index_path.write_text(
            json.dumps(
                [
                    {"id": "owner/repository", "note_title": "Existing", "file": "existing.md"},
                    {"id": "other/project", "note_title": "Fresh", "file": "fresh.md"},
                ]
            ),
            encoding="utf-8",
        )
        pending = feedstate.pending(monthly, index_path, batch_size=5)
        self.assertEqual(pending["checkpointed"], 1)
        self.assertEqual([entry["id"] for entry in pending["batch"]], ["other/project"])

    def test_legacy_per_scope_files_are_merged_into_shared_state(self):
        daily = "https://github.com/trending?since=daily"
        monthly = "https://github.com/trending?since=monthly"
        for scope, item_id in ((daily, "one/repo"), (monthly, "two/repo")):
            digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:20]
            path = Path(self.temporary.name) / f"github-trending-{digest}.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "scope": scope,
                        "seen_ids": [item_id],
                        "last_run": None,
                    }
                ),
                encoding="utf-8",
            )

        self.assertEqual(
            feedstate.filter_seen(
                "https://github.com/trending/go?since=weekly",
                ["one/repo", "two/repo", "three/repo"],
            ),
            ["three/repo"],
        )

        feedstate.mark_seen("https://github.com/trending/go?since=weekly", ["three/repo"])
        shared_path, _ = feedstate.state_paths()
        shared = json.loads(Path(shared_path).read_text(encoding="utf-8"))
        self.assertEqual(shared["version"], 2)
        self.assertEqual(shared["scope"], "all-configurations")
        self.assertEqual(set(shared["seen_ids"]), {"one/repo", "two/repo", "three/repo"})
        self.assertEqual(shared["seen_ids"][-1], "three/repo")


if __name__ == "__main__":
    unittest.main()
