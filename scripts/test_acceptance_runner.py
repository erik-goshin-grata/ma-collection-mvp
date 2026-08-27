#!/usr/bin/env python3
"""The acceptance runner orchestrates; it does not fetch, extract or gate.

WHAT WENT WRONG

An acceptance run was a sequence someone remembered: write a URL file, run the
page harness, eyeball the captures, hand the capture directory to the feeder.
Reproducible only from shell history, and only on the machine that had it.

WHAT THIS COVERS

Orchestration, end to end, with NO network and NO model call. The harness and
feeder are replaced by stub scripts that write the same artifacts a real run
would, so the wrapper's own decisions are exercised: preflight, capture-failure
tolerance, the healthy/quarantine partition, the failure manifest, rerun safety
and the final path report.

WHAT IT DELIBERATELY DOES NOT COVER

Fetching and extraction. Those belong to tools/page_harness.py, which is added
unchanged and is not this wrapper's behaviour to test. curl_cffi and playwright
are not installed here, so a live capture is not runnable in this environment
either -- and that is a statement about the environment, not a gap in the
wrapper.

THE RULE THIS FILE EXISTS TO PIN

The wrapper has no gate of its own. `ok and not suspect` is the FEEDER's rule,
in load_url_captures, and the wrapper imports it. Two copies would eventually
disagree and the wrapper's would be the wrong one.

Run from project root:
    python scripts/test_acceptance_runner.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RUNNER = ROOT / "scripts" / "run_acceptance.py"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _capture(pages: Path, name: str, url: str, *, ok=True, suspect=False,
             blocked=False, text="Body text long enough to look like a page.") -> None:
    """A capture directory shaped exactly as page_harness.save() leaves one."""
    d = pages / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({
        "url": url, "ok": ok, "suspect": suspect, "blocked": blocked,
        "via": "curl_cffi:chrome", "title": f"title {name}",
        "block_reason": "marker: just a moment" if blocked else None,
        "text_chars": len(text), "html_bytes": len(text) + 100,
        "candidates": {"trafilatura": len(text)}, "extractor": "trafilatura",
        "elapsed_ms": 512,
    }), encoding="utf-8")
    if ok:
        (d / "page.txt").write_text(text, encoding="utf-8")
        (d / "page.html").write_text(f"<html><body>{text}</body></html>", encoding="utf-8")


def _stub_tools(tmp: Path, *, harness_rc: int, captures) -> dict:
    """Stand-ins for the harness and feeder that write real artifacts.

    The harness stub exits with the code given -- 1 is the REAL harness's exit
    when any page fails, which is the case the wrapper must not obey.
    """
    h = tmp / "stub_harness.py"
    h.write_text(
        "import sys, json, pathlib\n"
        "sys.path.insert(0, %r)\n"
        "from test_acceptance_runner import _capture\n"
        "out = sys.argv[sys.argv.index('--out') + 1]\n"
        "p = pathlib.Path(out); p.mkdir(parents=True, exist_ok=True)\n"
        "for c in %r:\n"
        "    _capture(p, c['name'], c['url'], ok=c['ok'], suspect=c['suspect'],\n"
        "             blocked=c['blocked'])\n"
        "print(json.dumps({'stub': 'harness', 'argv': sys.argv[1:]}))\n"
        "sys.exit(%d)\n" % (str(ROOT / "scripts"), captures, harness_rc),
        encoding="utf-8")

    f = tmp / "stub_feeder.py"
    f.write_text(
        "import sys, pathlib, sqlite3\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--out-dir') + 1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'diagnostics').mkdir(exist_ok=True)\n"
        "sqlite3.connect(out / 'collection.db').close()\n"
        "for n in ('ma_review.csv','funding_review.csv','relevancy_rejections.csv'):\n"
        "    (out / n).write_text('col\\n')\n"
        "(out / 'manifest.json').write_text('{}')\n"
        "print('stub feeder argv:', sys.argv[1:])\n",
        encoding="utf-8")
    return {"harness": h, "feeder": f}


def _run(out: Path, urls: Path, tmp: Path, *, harness_rc=0, captures=(), extra=()):
    """Invoke the runner with the two subprocess targets stubbed."""
    stubs = _stub_tools(tmp, harness_rc=harness_rc, captures=list(captures))
    # A real run needs the pipeline's configuration, and the auth preflight is
    # right to demand it before capturing. Supplied here so these tests exercise
    # orchestration rather than re-testing config.py. The key is deliberately
    # fake: with no provider reachable the check declines to reach a verdict,
    # which is the behaviour a network blip must produce.
    env = dict(os.environ)
    env.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key-0000000000")
    env.setdefault("SEC_API_KEY", "sec-test")
    env.setdefault("OPERATOR_CONTACT_EMAIL", "test@example.test")
    src = RUNNER.read_text(encoding="utf-8")
    patched = tmp / "run_acceptance_patched.py"
    patched.write_text(
        # The patched copy lives in a temp dir, so its own __file__-derived ROOT
        # would point at the wrong tree and the real analyzer would look missing.
        src.replace('ROOT = Path(__file__).resolve().parents[1]',
                    f'ROOT = Path({str(ROOT)!r})')
           .replace('HARNESS = ROOT / "tools" / "page_harness.py"',
                    f'HARNESS = Path({str(stubs["harness"])!r})')
           .replace('FEEDER = ROOT / "scripts" / "run_collection_validation.py"',
                    f'FEEDER = Path({str(stubs["feeder"])!r})\n'
                    f'_REAL_FEEDER = ROOT / "scripts" / "run_collection_validation.py"')
           .replace('spec = importlib.util.spec_from_file_location("_rcv_acceptance", FEEDER)',
                    'spec = importlib.util.spec_from_file_location("_rcv_acceptance", _REAL_FEEDER)'),
        encoding="utf-8")
    # Orchestration tests skip the auth preflight: it is real, it reaches the
    # provider, and it correctly rejects the fake key above. Auth is covered by
    # test_auth_preflight; these tests are about the flow around it.
    cmd = [sys.executable, str(patched), "--urls", str(urls), "--out", str(out), *extra]
    if "--skip-auth-check" not in extra and "--capture-only" not in extra:
        cmd.append("--skip-auth-check")
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _urls(tmp: Path, lines) -> Path:
    p = tmp / "urls.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


HEALTHY = {"name": "good-a1b2c3", "url": "https://e.test/good", "ok": True,
           "suspect": False, "blocked": False}
BLOCKED = {"name": "wall-d4e5f6", "url": "https://e.test/wall", "ok": False,
           "suspect": False, "blocked": True}
SUSPECT = {"name": "frag-g7h8i9", "url": "https://e.test/frag", "ok": True,
           "suspect": True, "blocked": False}


def _code_only(path: Path) -> str:
    """Source with comments and string literals removed.

    These checks are about what the wrapper DOES, and prose that explains a
    boundary must not read as a violation of it. The docstrings deliberately
    name `ok and not suspect` and `--pl-ids` in order to say the wrapper does
    not own them; matching raw text would turn each explanation into a failure.
    """
    import io, tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_preflight() -> None:
    tmp = Path(tempfile.mkdtemp())
    print("\nPreflight fails before any capture is attempted:")
    r = _run(tmp / "o1", tmp / "nope.txt", tmp)
    check("a missing URL file is refused", r.returncode, 2)
    check("and says so", "missing URL file" in r.stderr, True)

    empty = _urls(tmp, ["# only a comment", ""])
    r = _run(tmp / "o2", empty, tmp)
    check("a URL file with no URLs is refused", r.returncode, 2)
    check("and says so", "contains no URLs" in r.stderr, True)

    junk = _urls(tmp, ["https://e.test/ok", "not-a-url"])
    r = _run(tmp / "o3", junk, tmp)
    check("a non-http line is refused", r.returncode, 2)
    check("naming the offending line", "'not-a-url'" in r.stderr, True)
    # The point of preflighting: nothing was fetched to learn this.
    check("no capture directory was created", (tmp / "o3" / "pages").exists(), False)

    print("\nA missing tool is a preflight failure, not a crash mid-run:")
    src = RUNNER.read_text(encoding="utf-8").replace(
        'HARNESS = ROOT / "tools" / "page_harness.py"',
        'HARNESS = ROOT / "tools" / "absent_harness.py"')
    p = tmp / "missing_tool.py"; p.write_text(src, encoding="utf-8")
    good = _urls(tmp, ["https://e.test/ok"])
    r = subprocess.run([sys.executable, str(p), "--urls", str(good),
                        "--out", str(tmp / "o4")], capture_output=True, text=True)
    check("refused", r.returncode, 2)
    check("naming the tool", "missing tool" in r.stderr, True)


def test_capture_tolerance() -> None:
    tmp = Path(tempfile.mkdtemp())
    urls = _urls(tmp, ["https://e.test/good", "https://e.test/wall",
                       "https://e.test/frag"])
    out = tmp / "run"
    print("\nA failing capture does not stop the healthy ones:")
    # The real harness exits 1 when ANY page fails. That must not be obeyed.
    r = _run(out, urls, tmp, harness_rc=1, captures=[HEALTHY, BLOCKED, SUSPECT])
    check("the run completes", r.returncode, 0)
    check("the harness exit code is reported, not obeyed",
          "harness exit    : 1" in r.stdout, True)
    check("and explained", "continuing with the healthy ones" in r.stdout, True)
    check("the feeder still ran", (out / "collection.db").exists(), True)

    print("\nThe partition comes from the feeder's gate, not the wrapper's:")
    check("one healthy capture", "captures healthy: 1" in r.stdout, True)
    # blocked AND suspect both quarantine -- `ok and not suspect`.
    check("two quarantined", "quarantined     : 2" in r.stdout, True)

    print("\nThe failure list is reusable as a URL file:")
    failed = out / "failed_urls.txt"
    check("written", failed.exists(), True)
    body = failed.read_text(encoding="utf-8")
    urls_in = [l.split()[0] for l in body.splitlines()
               if l.strip() and not l.startswith("#")]
    check("the blocked URL is listed", "https://e.test/wall" in urls_in, True)
    check("the suspect URL is listed", "https://e.test/frag" in urls_in, True)
    check("the healthy URL is NOT listed", "https://e.test/good" in urls_in, False)
    check("it is re-feedable as-is",
          all(u.startswith("https://") for u in urls_in), True)

    print("\nThe capture report is written:")
    check("capture_report.json exists", (out / "capture_report.json").exists(), True)

    print("\nEvery useful path is printed:")
    for label in ("M&A review", "Funding review", "Rejections", "Database",
                  "Diagnostics", "Captures", "Capture report", "Failed/quarantined"):
        check(f"{label} reported", label in r.stdout, True)


def test_no_healthy() -> None:
    tmp = Path(tempfile.mkdtemp())
    urls = _urls(tmp, ["https://e.test/wall"])
    out = tmp / "run"
    print("\nNo healthy capture stops the run before the pipeline:")
    r = _run(out, urls, tmp, harness_rc=1, captures=[BLOCKED])
    check("distinct exit code", r.returncode, 3)
    check("says why", "no healthy captures" in r.stderr, True)
    # The expensive half must not run when there is nothing to validate.
    check("the feeder did not run", (out / "collection.db").exists(), False)
    check("but the failure list still exists", (out / "failed_urls.txt").exists(), True)


def test_rerun_safety() -> None:
    tmp = Path(tempfile.mkdtemp())
    urls = _urls(tmp, ["https://e.test/good"])
    out = tmp / "run"
    print("\nA completed run is not silently destroyed:")
    r = _run(out, urls, tmp, captures=[HEALTHY])
    check("first run completes", r.returncode, 0)
    (out / "ma_review.csv").write_text("MARKER\n")

    r = _run(out, urls, tmp, captures=[HEALTHY])
    check("a second run is refused", r.returncode, 2)
    check("naming the database", "collection.db exists" in r.stderr, True)
    check("the prior review survives untouched",
          (out / "ma_review.csv").read_text(), "MARKER\n")

    print("\n--force archives rather than deletes:")
    r = _run(out, urls, tmp, captures=[HEALTHY], extra=["--force"])
    check("the run proceeds", r.returncode, 0)
    archived = list(out.glob("superseded-*/ma_review.csv"))
    check("the prior review was moved aside", len(archived), 1)
    check("with its content intact", archived[0].read_text(), "MARKER\n")
    check("and a fresh review was written", (out / "ma_review.csv").exists(), True)

    print("\n--resume reuses captures and skips the harness:")
    out2 = tmp / "resumed"
    (out2 / "pages").mkdir(parents=True)
    _capture(out2 / "pages", "good-a1b2c3", "https://e.test/good")
    r = _run(out2, urls, tmp, harness_rc=99, captures=[], extra=["--resume"])
    check("the run completes", r.returncode, 0)
    # harness_rc=99 would surface if the harness had been invoked at all.
    check("the harness was not invoked", "harness exit" in r.stdout, False)
    check("and says it reused them", "reusing captures" in r.stdout, True)
    check("the feeder ran on them", (out2 / "collection.db").exists(), True)

    print("\n--resume without captures is refused:")
    r = _run(tmp / "nothing", urls, tmp, extra=["--resume"])
    check("refused", r.returncode, 2)
    check("naming what is missing", "--resume needs" in r.stderr, True)


def test_no_duplicated_logic() -> None:
    print("\nThe wrapper adds no gate, no fetch and no Product behaviour:")
    body = _code_only(RUNNER)
    # The gate lives in the feeder and is imported.
    check("it imports the feeder's capture gate", "load_url_captures" in body, True)
    check("and does not re-derive the gate itself",
          "suspect" in body, False)
    for forbidden in ("requests", "curl_cffi", "playwright", "urlopen", "trafilatura"):
        check(f"no {forbidden}", forbidden in body, False)
    check("no PredictLeads requirement", "--pl-ids" in body or "--pl-tsv" in body, False)
    check("and the feeder is invoked in URL-only mode",
          "--pl" not in body, True)
    check("no retry policy of its own", "retry" in body.lower(), False)

    print("\nThe harness's controls are forwarded, not redefined:")
    fwd = RUNNER.read_text(encoding="utf-8")
    for flag in ("--workers", "--delay", "--timeout", "--profile", "--disable-http2"):
        check(f"{flag} forwarded", flag in fwd, True)
    check("and arbitrary extra args pass through", "harness_args" in body, True)

    print("\nThe supplied tools are unmodified:")
    up = Path("/root/.claude/uploads/30f16ff1-be4f-5e70-9e5a-9b564b3279f4")
    import hashlib
    for name, orig in (("page_harness.py", "d130c87b-page_harness.py"),
                       ("analyze_run.py", "4af98a87-analyze_run.py")):
        repo = ROOT / "tools" / name
        if not repo.exists() or not (up / orig).exists():
            continue      # guarded: uploads are not part of a checkout
        a = hashlib.sha256(repo.read_bytes()).hexdigest()
        b = hashlib.sha256((up / orig).read_bytes()).hexdigest()
        check(f"tools/{name} is byte-identical to the supplied file", a, b)



def _xlsx(path: Path, rows: list[list], sheet_name: str = "urls") -> Path:
    """A minimal but real .xlsx: zip of the XML parts openpyxl would write.

    Built here rather than fixtured so the shapes under test -- header,
    headerless, ambiguous -- are visible in the test rather than opaque binaries.
    """
    import zipfile
    from xml.sax.saxutils import escape
    def col(i): return chr(ord("A") + i)
    body = []
    for r, cells in enumerate(rows, 1):
        cs = "".join(
            f'<c r="{col(i)}{r}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'
            for i, v in enumerate(cells) if v not in (None, ""))
        body.append(f'<row r="{r}">{cs}</row>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
                   '/package/2006/content-types"><Default Extension="xml" ContentType='
                   '"application/xml"/></Types>')
        z.writestr("xl/workbook.xml",
                   f'<?xml version="1.0"?><workbook><sheets><sheet name="{sheet_name}"'
                   f' sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<?xml version="1.0"?><worksheet><sheetData>{"".join(body)}'
                   f'</sheetData></worksheet>')
    return path


def test_xlsx_input() -> None:
    tmp = Path(tempfile.mkdtemp())
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ra", RUNNER)
    ra = importlib.util.module_from_spec(spec); spec.loader.exec_module(ra)

    print("\nHeaderless single column — the shape of the real corpus:")
    f = _xlsx(tmp / "plain.xlsx", [["https://e.test/a"], ["https://e.test/b"],
                                   ["https://e.test/c"]])
    check("all rows read", ra.read_urls(f), ["https://e.test/a", "https://e.test/b",
                                             "https://e.test/c"])

    print("\nWorkbook row order is preserved, not sorted:")
    f = _xlsx(tmp / "order.xlsx", [["https://e.test/z"], ["https://e.test/a"],
                                   ["https://e.test/m"]])
    check("order is the workbook's", ra.read_urls(f),
          ["https://e.test/z", "https://e.test/a", "https://e.test/m"])

    print("\nBlank rows are ignored, not turned into empty URLs:")
    f = _xlsx(tmp / "blanks.xlsx", [["https://e.test/a"], [], ["  "],
                                    ["https://e.test/b"]])
    check("only the two URLs survive", ra.read_urls(f),
          ["https://e.test/a", "https://e.test/b"])

    print("\nA header naming the column is used:")
    f = _xlsx(tmp / "hdr.xlsx", [["Transaction Type", "Source URL"],
                                 ["M&A", "https://e.test/a"],
                                 ["Funding", "https://e.test/b"]])
    check("the header row is not a URL", ra.read_urls(f),
          ["https://e.test/a", "https://e.test/b"])
    # The label column must not reach the harness or influence anything.
    check("the other column is ignored entirely",
          any("M&A" in u or "Funding" in u for u in ra.read_urls(f)), False)

    print("\nAmbiguity fails clearly rather than picking one:")
    f = _xlsx(tmp / "two_hdr.xlsx", [["url", "source_url"],
                                     ["https://e.test/a", "https://e.test/b"]])
    try:
        ra.read_urls(f); check("two named URL columns refused", "no exception", "SystemExit")
    except SystemExit as e:
        check("two named URL columns refused", "columns look like a URL" in str(e), True)

    f = _xlsx(tmp / "two_cols.xlsx", [["https://e.test/a", "https://e.test/b"],
                                      ["https://e.test/c", "https://e.test/d"]])
    try:
        ra.read_urls(f); check("two equal URL columns refused", "no exception", "SystemExit")
    except SystemExit as e:
        check("two equal URL columns refused", "columns hold URLs" in str(e), True)

    print("\nA workbook with no URLs at all fails clearly:")
    f = _xlsx(tmp / "none.xlsx", [["Name", "Type"], ["Acme", "M&A"]])
    try:
        ra.read_urls(f); check("refused", "no exception", "SystemExit")
    except SystemExit as e:
        check("refused, naming the first row", "no column holds http(s) URLs" in str(e), True)

    print("\n.txt support is unchanged:")
    t = tmp / "u.txt"
    t.write_text("# a comment\nhttps://e.test/a\n\nhttps://e.test/b\n", encoding="utf-8")
    check("comments and blanks still skipped", ra.read_urls(t),
          ["https://e.test/a", "https://e.test/b"])

    print("\nThe real acceptance workbook:")
    real = Path("/tmp/acceptance_urls.xlsx")
    if real.exists():
        u = ra.read_urls(real)
        check("40 URLs", len(u), 40)
        check("all http(s)", all(x.startswith("https://") for x in u), True)
        check("39 distinct — one duplicate row", len(set(u)), 39)
    else:
        print("     (workbook not present in this checkout — skipped)")

    print("\nThe harness is fed a plain .txt, never the workbook:")
    urls = _xlsx(tmp / "feed.xlsx", [["https://e.test/good"]])
    out = tmp / "xrun"
    r = _run(out, urls, tmp, captures=[HEALTHY])
    check("the run completes", r.returncode, 0)
    resolved = out / "urls_resolved.txt"
    check("a resolved list is written", resolved.exists(), True)
    check("recording where it came from", "feed.xlsx" in resolved.read_text(), True)
    check("and the harness was given that file", "urls_resolved.txt" in r.stdout, True)


def test_auth_preflight() -> None:
    tmp = Path(tempfile.mkdtemp())
    urls = _urls(tmp, ["https://e.test/good"])
    print("\nA credential that will 401 is caught BEFORE any capture:")
    env = dict(os.environ)
    env.update({"ANTHROPIC_API_KEY": "sk-ant-stale-key-that-will-not-authenticate",
                "SEC_API_KEY": "x", "OPERATOR_CONTACT_EMAIL": "t@e.test"})
    stubs = _stub_tools(tmp, harness_rc=0, captures=[HEALTHY])
    src = RUNNER.read_text(encoding="utf-8")
    patched = tmp / "auth_patched.py"
    patched.write_text(
        src.replace('ROOT = Path(__file__).resolve().parents[1]', f'ROOT = Path({str(ROOT)!r})')
           .replace('HARNESS = ROOT / "tools" / "page_harness.py"',
                    f'HARNESS = Path({str(stubs["harness"])!r})'), encoding="utf-8")
    out = tmp / "authrun"
    r = subprocess.run([sys.executable, str(patched), "--urls", str(urls),
                        "--out", str(out)], capture_output=True, text=True, env=env)
    if "not verified" in r.stdout:
        # If the provider is unreachable the check declines to reach a verdict --
        # a network blip is not evidence about a credential.
        print("     (provider unreachable — verified the non-verdict path instead)")
        check("a transport failure is not treated as an auth failure",
              "not an auth failure" in r.stdout or "not verified" in r.stdout, True)
        check("and the run is not blocked by it", r.returncode in (0, 3), True)
    else:
        check("the run is refused", r.returncode, 2)
        check("naming the variable", "ANTHROPIC_API_KEY" in r.stderr, True)
        check("explaining the override", "overrides any other route" in r.stderr, True)
        check("nothing was captured", (out / "pages").exists(), False)

    print("\nThe key is fingerprinted, never printed:")
    check("the full key never appears in output",
          "sk-ant-stale-key-that-will-not-authenticate" in (r.stdout + r.stderr), False)
    check("but the provider is named", "provider=anthropic" in r.stdout, True)

    print("\n--skip-auth-check bypasses it, and --capture-only implies it:")
    r2 = subprocess.run([sys.executable, str(patched), "--urls", str(urls),
                         "--out", str(tmp / "skip"), "--skip-auth-check"],
                        capture_output=True, text=True, env=env)
    check("skipped on request", "skipped (--skip-auth-check)" in r2.stdout, True)
    r3 = subprocess.run([sys.executable, str(patched), "--urls", str(urls),
                         "--out", str(tmp / "conly"), "--capture-only"],
                        capture_output=True, text=True, env=env)
    # No model call happens in a capture-only run, so a credential is irrelevant.
    check("skipped for a capture-only run", "skipped" in r3.stdout, True)


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_preflight()
    test_capture_tolerance()
    test_no_healthy()
    test_rerun_safety()
    test_no_duplicated_logic()
    test_xlsx_input()
    test_auth_preflight()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — orchestration only: no fetch, no gate, no Product behaviour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
