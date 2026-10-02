import contextlib, io, json, sys, tempfile, unittest
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


    def test_suggest_moved_file(self):
        d = make_repo("Edit `src/server.py` now.\n", {"src/app/server.py": "", "src/x.py": ""})
        f = [x for x in rr.audit(d, d / "README.md", use_git=False) if x.kind == "missing-file"]
        self.assertEqual(f[0].suggestion, "src/app/server.py")

    def test_suggest_renamed_file(self):
        d = make_repo("See `src/serer.py`.\n", {"src/server.py": ""})
        f = rr.audit(d, d / "README.md", use_git=False)
        self.assertEqual(f[0].suggestion, "src/server.py")

    def test_suggest_script_and_target(self):
        d = make_repo("```\nnpm run biuld\nmake instal\n```\n",
                      {"package.json": json.dumps({"scripts": {"build": "x"}}),
                       "Makefile": "install:\n\techo\n"})
        sug = {f.kind: f.suggestion for f in rr.audit(d, d / "README.md", use_git=False)}
        self.assertEqual(sug["missing-script"], "build")
        self.assertEqual(sug["missing-make-target"], "install")

    def test_no_suggestion_when_nothing_close(self):
        d = make_repo("See `src/zzzzzz.py`.\n", {"src/real.py": ""})
        f = rr.audit(d, d / "README.md", use_git=False)
        self.assertEqual(f[0].suggestion, "")

    def test_github_annotation_format(self):
        f = rr.Finding("error", 3, "broken-link", "a, b: c\nd", suggestion="x")
        self.assertEqual(
            rr.github_annotation(f, "README.md"),
            "::error file=README.md,line=3,title=readme-rot%3A broken-link::a, b: c%0Ad (did you mean x?)")
        w = rr.Finding("warn", 0, "drift", "old")
        self.assertEqual(rr.github_annotation(w, "README.md"),
                         "::warning file=README.md,title=readme-rot%3A drift::old")

    def test_github_format_cli(self):
        d = make_repo("[a](nope.md)\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rr.main([str(d), "--format", "github", "--no-git"])
        self.assertEqual(code, 1)
        self.assertTrue(buf.getvalue().startswith("::error file="))

    def test_ignore_markers(self):
        readme = ("<!-- readme-rot: off -->\n`gone/a.py`\n```\nnpm run nope\n```\n"
                  "<!-- readme-rot: on -->\n"
                  "`gone/b.py` <!-- readme-rot: ignore -->\n"
                  "`gone/c.py`\n")
        d = make_repo(readme)
        self.assertEqual({s for _, s in kinds(d)}, {"gone/c.py"})

    def test_markers_in_code_do_not_trigger(self):
        readme = ("Wrap examples in `<!-- readme-rot: off -->`.\n"
                  "```\n<!-- readme-rot: off -->\n```\n"
                  "`gone/c.py`\n")
        d = make_repo(readme)
        self.assertIn(("missing-file", "gone/c.py"), kinds(d))


if __name__ == "__main__":
    unittest.main()
