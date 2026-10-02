import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import readme_rot as rr


def make_repo(readme, files=None):
    d = Path(tempfile.mkdtemp())
    (d / "README.md").write_text(readme)
    for name, content in (files or {}).items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


def kinds(d):
    return {(f.kind, f.snippet) for f in rr.audit(d, d / "README.md", use_git=False)}


class Tests(unittest.TestCase):
    def test_clean_readme(self):
        d = make_repo("# Hi\n\nSee [x](src/a.py) and `src/a.py`.\n", {"src/a.py": ""})
        self.assertEqual(rr.audit(d, d / "README.md", use_git=False), [])

    def test_broken_link_and_anchor(self):
        d = make_repo("# Hi\n[a](nope.md) [b](#missing) [c](#hi)\n")
        k = kinds(d)
        self.assertIn(("broken-link", "nope.md"), k)
        self.assertIn(("broken-anchor", "#missing"), k)
        self.assertNotIn(("broken-anchor", "#hi"), k)

    def test_missing_path(self):
        d = make_repo("Edit `src/gone.py` now.\n", {"src/real.py": ""})
        self.assertIn(("missing-file", "src/gone.py"), kinds(d))

    def test_false_positives_ignored(self):
        d = make_repo("Use Node.js, `read/write`, `path/to/file.txt`, `/usr/bin/env`, `1.2.3`.\n")
        self.assertEqual(rr.audit(d, d / "README.md", use_git=False), [])

    def test_npm_and_make(self):
        d = make_repo("```\nnpm run build\nmake ship\n```\n",
                      {"package.json": json.dumps({"scripts": {"dev": "x"}}),
                       "Makefile": "all:\n\techo\n"})
        k = kinds(d)
        self.assertTrue(any(x[0] == "missing-script" for x in k))
        self.assertTrue(any(x[0] == "missing-make-target" for x in k))

    def test_cd_lines_skipped(self):
        d = make_repo("```\ncd web && npm run frontend\n```\n")
        self.assertEqual(rr.audit(d, d / "README.md", use_git=False), [])

    def test_ghost_env(self):
        d = make_repo("Set `DB_URL_MAIN` and `API_KEY`.\n", {"app.py": "os.environ['API_KEY']"})
        k = kinds(d)
        self.assertIn(("ghost-env", "DB_URL_MAIN"), k)
        self.assertNotIn(("ghost-env", "API_KEY"), k)

    def test_score(self):
        self.assertEqual(rr.score_of([]), 100)
        self.assertEqual(rr.score_of([rr.Finding("error", 1, "x", "m")] * 20), 0)


if __name__ == "__main__":
    unittest.main()
