#!/usr/bin/env python3
"""readme-rot: find the lies in your README.

Checks a README against the repository it describes and reports claims that
are no longer true: dead links, missing files, nonexistent npm scripts and
make targets, env vars nothing reads, and how far the code has drifted since
the README was last touched.

Zero dependencies. Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote

__version__ = "0.1.0"

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    "target", ".tox", ".mypy_cache", ".next", "vendor", ".idea", ".vscode",
}
README_NAMES = ["README.md", "readme.md", "Readme.md", "README.markdown", "README"]
EXT = (r"py|js|mjs|cjs|ts|tsx|jsx|json|ya?ml|toml|md|sh|go|rs|rb|java|kt|c|h|"
       r"cpp|hpp|cs|php|txt|cfg|ini|lock|html|css|scss|sql|xml|csv")
SYSTEM_DIRS = {"usr", "etc", "var", "tmp", "home", "opt", "dev", "proc", "bin", "sys"}
PLACEHOLDERS = {
    "path", "to", "your", "my", "foo", "bar", "baz", "file", "filename", "name",
    "dir", "folder", "directory", "example", "sample", "xxx", "myfile", "myproject",
}
# "Node.js" in backticks is not a file.
FRAMEWORK_STEMS = {
    "node", "next", "vue", "nuxt", "react", "express", "three", "d3", "chart",
    "ember", "backbone", "alpine", "p5", "moment", "lodash", "angular", "socket",
    "nest", "deno", "bun",
}

FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?\s*$")
HTML_RE = re.compile(r"""(?:src|href)=["']([^"']+)["']""", re.I)
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
ANCHOR_RE = re.compile(r"""(?:id|name)=["']([^"']+)["']""", re.I)
PATHLIKE_RE = re.compile(r"(?:\.{1,2}/)*[\w@.\-]+(?:/[\w@.\-]+)*/?")
ENV_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
UPPER_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

NPM_RUN_RE = re.compile(r"(?:sudo\s+)?(?:npm|pnpm|yarn|bun)\s+run(?:-script)?\s+([\w:.\-]+)")
NPM_TEST_RE = re.compile(r"(?:sudo\s+)?(?:npm|pnpm|yarn)\s+(?:test|t)\b")
MAKE_RE = re.compile(r"make\s+(?:-\S+\s+)*([A-Za-z_][\w.\-]*)(?:\s|$)")
RUN_FILE_RE = re.compile(
    r"(?:sudo\s+)?(?:python3?|py|node|deno\s+run|bash|sh|zsh|ruby|perl|php|tsx|"
    r"ts-node|npx\s+tsx|npx\s+ts-node)\s+(?:-[\w\-]+\s+)*([\w./\-]+)(?:\s|$)"
)
DOT_SLASH_RE = re.compile(r"\./([\w./\-]+)")


@dataclass
class Finding:
    level: str  # error | warn | info
    line: int
    kind: str
    message: str
    snippet: str = ""


def slugify(heading: str) -> str:
    """Approximate GitHub's heading -> anchor slug."""
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    h = h.replace("`", "").replace("*", "").strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors_of(text: str) -> set:
    slugs, seen, in_fence = set(), {}, False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            s = slugify(m.group(1))
            n = seen.get(s, 0)
            seen[s] = n + 1
            slugs.add(s if n == 0 else f"{s}-{n}")
        slugs.update(ANCHOR_RE.findall(line))
    return slugs


class Repo:
    """Index of the repository around a README."""

    def __init__(self, root: Path, readme: Path):
        self.root, self.readme, self.rdir = root, readme, readme.parent
        self.files, self.dirs, self.base = set(), set(), set()
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            rel = os.path.relpath(dp, root)
            if rel != ".":
                self.dirs.add(Path(rel).as_posix())
            for f in fn:
                self.files.add((Path(dp) / f).relative_to(root).as_posix())
                self.base.add(f)
        self.scripts, self.make_targets = set(), set()
        self.has_pkg = self.has_make = False
        for rel in self.files:
            name = rel.rsplit("/", 1)[-1]
            if name == "package.json" and rel.count("/") <= 3:
                self.has_pkg = True
                try:
                    data = json.loads((root / rel).read_text(encoding="utf-8"))
                    self.scripts.update((data.get("scripts") or {}).keys())
                except (OSError, ValueError):
                    pass
            elif name in ("Makefile", "makefile", "GNUmakefile") and rel.count("/") <= 3:
                self.has_make = True
                self.make_targets.update(parse_make_targets((root / rel)))
        self._corpus = None

    def exists(self, rel: str) -> bool:
        rel = rel.rstrip("/")
        return any((b / rel).exists() for b in (self.root, self.rdir))

    def is_repo_dir(self, name: str) -> bool:
        return (self.root / name).is_dir()

    def code_tokens(self) -> set:
        """All UPPER_CASE tokens in non-doc source files (for ghost env var detection)."""
        if self._corpus is None:
            toks = set()
            for rel in self.files:
                if rel.lower().endswith((".md", ".markdown", ".rst")):
                    continue
                p = self.root / rel
                try:
                    if p.stat().st_size > 512_000:
                        continue
                    data = p.read_bytes()
                except OSError:
                    continue
                if b"\0" in data:
                    continue
                toks.update(UPPER_TOKEN_RE.findall(data.decode("utf-8", "ignore")))
            self._corpus = toks
        return self._corpus


def parse_make_targets(path: Path) -> set:
    out = set()
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line or line[0] in "\t #":
                continue
            m = re.match(r"^([^:=#]+?)\s*:(?!=)", line)
            if m:
                out.update(t for t in m.group(1).split() if "%" not in t)
    except OSError:
        pass
    return out


def check_link(target, lineno, repo, own_slugs, out):
    t = target.strip()
    if not t or re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", t) or t.startswith("//"):
        return
    path, _, frag = t.partition("#")
    path = unquote(path.split("?")[0])
    if frag and re.match(r"^L\d+", frag):
        frag = ""
    if not path:
        if frag and frag.lower() not in own_slugs:
            out.append(Finding("error", lineno, "broken-anchor",
                               f"anchor #{frag} matches no heading", t))
        return
    base = repo.root if path.startswith("/") else repo.rdir
    full = base / path.lstrip("/")
    if not full.exists():
        out.append(Finding("error", lineno, "broken-link", f"{path} does not exist", t))
    elif frag and full.is_file() and full.suffix.lower() == ".md":
        try:
            if frag.lower() not in anchors_of(full.read_text(encoding="utf-8", errors="ignore")):
                out.append(Finding("error", lineno, "broken-anchor",
                                   f"{path} has no heading #{frag}", t))
        except OSError:
            pass


def check_pathlike(code, lineno, repo, out):
    if not PATHLIKE_RE.fullmatch(code):
        return
    core = code.rstrip("/")
    has_slash = "/" in core
    has_ext = bool(re.search(rf"\.({EXT})$", core, re.I)) and not code.endswith("/")
    trailing = code.endswith("/")
    dotted = code.startswith("./") or code.startswith("../")
    if not (has_slash or has_ext) or re.fullmatch(r"\d+(\.\d+)+", core):
        return
    first = core.split("/")[0]
    if first in SYSTEM_DIRS and not repo.is_repo_dir(first):
        return
    if repo.exists(code):
        return
    segs = [s.rsplit(".", 1)[0].lower() for s in core.split("/")]
    if any(s in PLACEHOLDERS for s in segs):
        return
    if not has_slash:  # bare filename: fine if it exists anywhere in the repo
        if code in repo.base or segs[0] in FRAMEWORK_STEMS:
            return
        out.append(Finding("warn", lineno, "missing-file",
                           f"{code} not found anywhere in the repo (generated file?)", code))
        return
    if has_ext or trailing or dotted or repo.is_repo_dir(first):
        out.append(Finding("error", lineno, "missing-file", f"{code} does not exist", code))


def check_command(part, lineno, repo, out):
    part = part.strip()
    m = NPM_RUN_RE.match(part)
    if m:
        s = m.group(1)
        if not repo.has_pkg:
            out.append(Finding("error", lineno, "missing-script",
                               f"README runs `{part}` but there is no package.json", part))
        elif s not in repo.scripts:
            out.append(Finding("error", lineno, "missing-script",
                               f'no "{s}" script in package.json', part))
        return
    if NPM_TEST_RE.match(part):
        if repo.has_pkg and "test" not in repo.scripts:
            out.append(Finding("error", lineno, "missing-script",
                               'no "test" script in package.json', part))
        return
    m = MAKE_RE.match(part)
    if m:
        t = m.group(1)
        if not repo.has_make:
            out.append(Finding("error", lineno, "missing-make-target",
                               f"README runs `{part}` but there is no Makefile", part))
        elif t not in repo.make_targets:
            out.append(Finding("error", lineno, "missing-make-target",
                               f"no `{t}` target in Makefile", part))
        return
    m = RUN_FILE_RE.match(part)
    if m and not re.search(r"\s-m\s", part):
        tok = m.group(1)
        if re.search(rf"\.({EXT})$", tok, re.I):
            if not repo.exists(tok) and tok.rsplit("/", 1)[-1] not in repo.base:
                if tok.split("/")[0].rsplit(".", 1)[0].lower() not in PLACEHOLDERS:
                    out.append(Finding("error", lineno, "missing-file",
                                       f"{tok} does not exist", part))
        return
    m = DOT_SLASH_RE.match(part)
    if m and re.search(rf"\.({EXT})$", m.group(1), re.I):
        if not repo.exists(m.group(1)):
            out.append(Finding("error", lineno, "missing-file",
                               f"./{m.group(1)} does not exist", part))


def run_git(root, *args):
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def check_drift(repo, out):
    rel = repo.readme.relative_to(repo.root).as_posix()
    last = run_git(repo.root, "log", "-1", "--format=%H %ct", "--", rel)
    if not last:
        return
    sha, ts = last.split()
    n = run_git(repo.root, "rev-list", "--count", f"{sha}..HEAD", "--", ".",
                ":(exclude)*.md")
    if n is None:
        return
    n, days = int(n), int((time.time() - int(ts)) / 86400)
    if n == 0:
        return
    level = "warn" if n >= 25 else "info"
    out.append(Finding(level, 0, "drift",
                       f"README last changed {days} days ago; {n} commits to "
                       f"non-doc files since"))


def audit(root: Path, readme: Path, use_git=True):
    repo = Repo(root, readme)
    text = readme.read_text(encoding="utf-8", errors="ignore")
    own_slugs = anchors_of(text)
    out, seen_env, in_fence = [], set(), False
    for i, raw in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            line = re.sub(r"^\s*[$>%]\s+", "", raw).strip()
            if not line or line.startswith("#") or re.search(r"(^|\s|&&|;)cd\s", line):
                continue
            for part in re.split(r"&&|\|\||;", line):
                check_command(part, i, repo, out)
            continue
        blanked = INLINE_RE.sub(lambda m: " " * len(m.group(0)), raw)
        targets = LINK_RE.findall(blanked) + HTML_RE.findall(blanked)
        ref = REF_RE.match(raw)
        if ref:
            targets.append(ref.group(1))
        for t in targets:
            check_link(t, i, repo, own_slugs, out)
        for code in INLINE_RE.findall(raw):
            code = code.strip()
            if re.search(r"(^|\s|&&|;)cd\s", code):
                continue
            check_pathlike(code, i, repo, out)
            for part in re.split(r"&&|\|\||;", code):
                check_command(part, i, repo, out)
            if ENV_RE.fullmatch(code) and code not in seen_env:
                seen_env.add(code)
                if code not in repo.code_tokens():
                    out.append(Finding("warn", i, "ghost-env",
                                       f"{code} is documented but appears nowhere in the code",
                                       code))
    if use_git:
        check_drift(repo, out)
    uniq, keys = [], set()
    for f in out:
        k = (f.kind, f.message)
        if k not in keys:
            keys.add(k)
            uniq.append(f)
    return uniq


def score_of(findings):
    e = sum(f.level == "error" for f in findings)
    w = sum(f.level == "warn" for f in findings)
    return max(0, 100 - 12 * e - 4 * w)


def verdict(score):
    if score == 100:
        return "Fresh"
    if score >= 85:
        return "Mostly fresh"
    if score >= 60:
        return "Getting stale"
    if score >= 30:
        return "Rotting"
    return "Compost"


def badge_url(score):
    color = ("brightgreen" if score >= 90 else "green" if score >= 75 else
             "yellow" if score >= 60 else "orange" if score >= 30 else "red")
    return f"https://img.shields.io/badge/readme%20freshness-{score}%25-{color}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="readme-rot", description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?", default=".", help="repo directory or README file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--badge", action="store_true", help="print a shields.io badge for your README")
    ap.add_argument("--fail-under", type=int, metavar="N",
                    help="exit 1 if score < N (default: exit 1 on any error)")
    ap.add_argument("--no-git", action="store_true", help="skip git drift analysis")
    ap.add_argument("--version", action="version", version=f"readme-rot {__version__}")
    a = ap.parse_args(argv)

    p = Path(a.path).resolve()
    if p.is_file():
        root, readme = p.parent, p
    else:
        root = p
        readme = next((root / n for n in README_NAMES if (root / n).is_file()), None)
        if readme is None:
            print("readme-rot: no README found", file=sys.stderr)
            return 2

    findings = audit(root, readme, use_git=not a.no_git)
    score = score_of(findings)

    if a.json:
        print(json.dumps({"readme": readme.name, "score": score, "verdict": verdict(score),
                          "findings": [asdict(f) for f in findings]}, indent=2))
    else:
        color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        paint = (lambda c, s: f"\033[{c}m{s}\033[0m") if color else (lambda c, s: s)
        icon = {"error": paint("31", "x"), "warn": paint("33", "!"), "info": paint("36", "i")}
        print(f"readme-rot {__version__}: checking {readme.name}\n")
        order = {"error": 0, "warn": 1, "info": 2}
        for f in sorted(findings, key=lambda f: (order[f.level], f.line)):
            loc = f"line {f.line:<4}" if f.line else "         "
            print(f"  {icon[f.level]} {loc} {f.kind:<20} {f.message}")
        if not findings:
            print("  Nothing rotten. Your README is telling the truth.")
        print(f"\nFreshness: {paint('1', f'{score}/100')} ({verdict(score)})")
        if a.badge:
            print(f"\n![README freshness]({badge_url(score)})")

    if a.fail_under is not None:
        return 1 if score < a.fail_under else 0
    return 1 if any(f.level == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
