# README-rot

**Your README is lying. Find out where.**

Code changes every day. READMEs don't. `README-rot` checks your README against the repo it describes and reports every claim that is no longer true, then gives you a freshness score and a badge.

Zero dependencies. One file. Python 3.8+.

## What it catches

<!-- readme-rot: off -->

|Check|Example|
|-|-|
|Broken links and anchors|`[guide](docs/guide.md#setup)` where that heading no longer exists|
|Missing files|``src/server.py`` was renamed three refactors ago|
|Dead `npm run` scripts|`npm run start` but `package.json` has no `start`|
|Dead `make` targets|`make deploy` but the Makefile has no `deploy`|
|Missing scripts in commands|`python scripts/migrate.py` points at nothing|
|Ghost env vars|`DATABASE_URL_PRIMARY` is documented but no code reads it|
|Drift|README untouched for 300 days while 400 commits landed|

<!-- readme-rot: on -->

Every broken path comes with a **did you mean** hint when a moved or renamed file looks like a match.

It is built to avoid false positives: `Node.js`, `read/write`, `path/to/file.txt`, `/usr/bin/env`, and lines containing `cd` are all ignored.

## Install and run

You need Python 3.8 or newer. On Windows, if you don't have it:

```powershell
winget install Python.Python.3.12
```

Restart your terminal afterwards so `python` is on your PATH.

Then either run the single file directly (no setup):

```bash
python readme_rot.py
```

or install the `readme-rot` command with pipx:

```bash
pipx install .
```

## Usage

```bash
readme-rot              # audit the current repo
readme-rot --badge      # also print a shields.io badge
readme-rot --json       # for tooling
readme-rot --format github   # inline annotations in GitHub Actions
readme-rot --fail-under 80   # CI gate
```

If you ran the file directly, use `python readme_rot.py` in place of `readme-rot`, for example `python readme_rot.py --badge`.

## Example

```
readme-rot 0.1.0: checking README.md

  x line 3    broken-anchor        docs/guide.md has no heading #setup
  x line 3    broken-link          docs/old.md does not exist
  x line 3    broken-anchor        anchor #nowhere matches no heading
  x line 7    missing-script       no "start" script in package.json
  x line 7    missing-file         src/server.py does not exist
  x line 12   missing-make-target  no `deploy` target in Makefile
  x line 13   missing-file         scripts/migrate.py does not exist
  ! line 8    ghost-env            DATABASE_URL_PRIMARY is documented but appears nowhere in the code

Freshness: 12/100 (Compost)
```

## Use it in CI

Add this to `.github/workflows/readme-rot.yml`. Problems show up as inline annotations on the README in your pull requests.

```yaml
name: readme-rot
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # needed for git drift analysis
      - uses: UE5test1/README-rot@v1
        with:
          fail-under: 80   # optional; default fails on any error
```

## Intentional fake examples

Docs about docs need examples that point at nothing. Tell `readme-rot` to look away:

```markdown
<!-- readme-rot: off -->
Anything here is skipped, e.g. `npm run start` in a tutorial.
<!-- readme-rot: on -->

One line: `src/example.py` <!-- readme-rot: ignore -->
```

## Scoring

`100 - 12 per error - 4 per warning`, floored at 0.

|Score|Verdict|
|-|-|
|100|Fresh|
|85+|Mostly fresh|
|60+|Getting stale|
|30+|Rotting|
|under 30|Compost|

Exit code is `1` if any error is found (or if the score is under `--fail-under`).

## Run the tests

```bash
python -m unittest discover -s tests
```

## Ideas / roadmap

* Check documented CLI flags against `argparse`/`click` definitions
* Verify `pip install` / `npm install` package names exist in manifests
* `--fix` mode that applies the "did you mean" suggestions for you
* Support for `.rst` and docs folders

PRs welcome.

## License

MIT
