#!/usr/bin/env python3
"""run_acceptance.py — one command for a URL-file acceptance run.

    python scripts/run_acceptance.py --urls acceptance_urls.txt \
        --out out/acceptance_20260827

Replaces the manual sequence: write a URL file, run the page harness, eyeball
the captures, hand the capture directory to the Collection feeder, wait. That
sequence was reproducible only in someone's shell history.

WHAT THIS IS

Orchestration and nothing else. Three tools already do the work and this calls
them in order:

    tools/page_harness.py             fetches each URL, writes a capture dir
    tools/analyze_run.py              diagnoses the capture fleet
    scripts/run_collection_validation.py   seeds and runs the pipeline

No fetching, no extraction, no gate of its own. It adds no retry policy, no
Business Wire recovery, no PredictLeads requirement, and no Product behaviour.
Every fetch control belongs to the harness and is passed straight through.

THE HEALTHY / QUARANTINE GATE IS NOT REIMPLEMENTED HERE. The feeder owns it --
`ok and not suspect`, in `load_url_captures` -- so this imports that function
rather than deciding for itself which captures are good. Two copies of that
rule would eventually disagree, and the wrapper's copy would be the wrong one.

FAILURES DO NOT STOP THE RUN. The harness exits 1 when ANY url fails, which is
correct for the harness and wrong as a gate here: one blocked page must not
strand thirty good ones. The exit code is reported and not obeyed. The run
stops only when NO capture is usable, because then there is nothing to validate.

RERUNS DO NOT DESTROY A COMPLETED RUN

    <out> is empty or absent          run normally
    <out>/pages exists, no DB         capture looks interrupted; --resume
                                      reuses it, otherwise re-run capture
    <out>/collection.db exists        a completed run. REFUSED by default.
                                      --force archives the previous outputs
                                      into <out>/superseded-<stamp>/ and
                                      proceeds; nothing is deleted.

The feeder refuses to append to an existing database on its own. That refusal
is left to surface rather than duplicated -- this checks earlier only so the
refusal arrives before a capture pass rather than after it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tools" / "page_harness.py"
ANALYZER = ROOT / "tools" / "analyze_run.py"
FEEDER = ROOT / "scripts" / "run_collection_validation.py"


def _load_feeder():
    """Import the feeder as a module, to reuse its capture gate.

    The same shape the test suite uses. Imported rather than re-implemented so
    `ok and not suspect` has exactly one definition in the tree.
    """
    spec = importlib.util.spec_from_file_location("_rcv_acceptance", FEEDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rcv_acceptance"] = mod
    spec.loader.exec_module(mod)
    return mod



# --------------------------------------------------------------------------
# input normalization -- a URL list is a URL list, whatever it arrived in
# --------------------------------------------------------------------------

_URL_HEADER_HINTS = ("url", "link", "source", "href")


def _xlsx_column(path: Path) -> list[str]:
    """URLs from a workbook, in row order.

    Read with `zipfile` and no third-party dependency, which is how this repo
    already handles xlsx -- scripts/export_review_xlsx.py writes one the same
    way, and requirements.txt declares no spreadsheet library.

    Input normalization only. Exactly one column is read; any others -- an M&A
    or Funding label, a note, an id -- are left alone, because nothing
    downstream is allowed to behave differently because of them yet.
    """
    import re as _re
    import zipfile

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            shared = [_re.sub(r"<.*?>", "", m) for m in _re.findall(
                r"<si>(.*?)</si>",
                z.read("xl/sharedStrings.xml").decode("utf-8", "replace"), _re.S)]
        sheets = sorted(n for n in names
                        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not sheets:
            raise SystemExit(f"  PREFLIGHT FAIL  {path} contains no worksheet")
        # First sheet only. A second sheet holding a different corpus would be a
        # different run, and silently concatenating them is how a corpus grows
        # without anyone deciding it should.
        body = z.read(sheets[0]).decode("utf-8", "replace")

    rows: list[dict[str, str]] = []
    for rm in _re.finditer(r"<row[^>]*>(.*?)</row>", body, _re.S):
        cells: dict[str, str] = {}
        for cm in _re.finditer(
                r'<c r="([A-Z]+)\d+"(?:[^>]*?t="(\w+)")?[^>]*?>'
                r'(?:<v>(.*?)</v>|<is><t[^>]*>(.*?)</t></is>)?', rm.group(1)):
            col, typ, v, inline = cm.groups()
            if typ == "s" and v is not None and v.isdigit() and int(v) < len(shared):
                val = shared[int(v)]
            else:
                val = inline if inline is not None else v
            if val and str(val).strip():
                cells[col] = str(val).strip()
        rows.append(cells)
    rows = [r for r in rows if r]          # blank rows carry no cells at all
    if not rows:
        raise SystemExit(f"  PREFLIGHT FAIL  {path} has no populated rows")

    def _is_url(v: str) -> bool:
        return v.lower().startswith(("http://", "https://"))

    # Two shapes, told apart by the first row rather than assumed. A header is a
    # row of labels; a headerless sheet starts with data. Guessing wrong either
    # drops a URL or feeds a label to the harness, so it is decided, not assumed.
    header = rows[0]
    named = [c for c, v in header.items()
             if any(h in v.lower() for h in _URL_HEADER_HINTS) and not _is_url(v)]
    if named:
        if len(named) > 1:
            raise SystemExit(
                f"  PREFLIGHT FAIL  {path}: {len(named)} columns look like a URL "
                f"column ({', '.join(f'{c}={header[c]!r}' for c in named)}). "
                f"Leave one, or supply a .txt file.")
        col, data = named[0], rows[1:]
    else:
        counts = {c: sum(1 for r in rows if _is_url(r.get(c, ""))) for c
                  in {c for r in rows for c in r}}
        best = [c for c, n in counts.items() if n]
        if not best:
            raise SystemExit(
                f"  PREFLIGHT FAIL  {path}: no column holds http(s) URLs and no "
                f"header names one. First row: "
                f"{', '.join(f'{c}={v!r}' for c, v in header.items())}")
        if len(best) > 1:
            top = sorted(best, key=lambda c: -counts[c])
            if counts[top[0]] == counts[top[1]]:
                raise SystemExit(
                    f"  PREFLIGHT FAIL  {path}: {len(best)} columns hold URLs "
                    f"({', '.join(f'{c} x{counts[c]}' for c in top)}). "
                    f"Add a header naming the one to use, or supply a .txt file.")
            best = top[:1]
        col, data = best[0], rows

    urls = [r[col] for r in data if r.get(col)]
    if not urls:
        raise SystemExit(f"  PREFLIGHT FAIL  {path}: column {col} holds no values")
    print(f"  input           : {path.name} sheet 1, column {col}"
          + (f" (header {header[col]!r})" if named else " (no header row)"))
    return urls


def read_urls(path: Path) -> list[str]:
    """The URL list, from .xlsx or .txt. Row/line order is preserved either way."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _xlsx_column(path)
    # Same reading the harness applies to --urls: blank lines and # comments out.
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def preflight_model_auth(skip: bool) -> None:
    """Fail on a credential that will 401, before a capture pass is paid for.

    THE FAILURE THIS EXISTS FOR. config.py requires ANTHROPIC_API_KEY to be
    non-empty and passes it explicitly to the SDK. Non-empty is not valid: a
    stale key passes configuration, overrides whatever route was intended --
    because an explicit key wins over an ambient one -- and then 401s at the
    first model call, which is after every page has already been fetched.

    No credential behaviour is invented here. The key is resolved by the repo's
    own get_config(), and verified by asking the provider whether it is good.
    models.list is the cheapest authenticated endpoint: it spends no tokens.
    """
    if skip:
        print("  model auth      : skipped (--skip-auth-check)")
        return
    sys.path.insert(0, str(ROOT))
    try:
        from config import get_config
    except Exception as exc:                                   # pragma: no cover
        print(f"  model auth      : not checked ({type(exc).__name__})")
        return
    try:
        cfg = get_config()
    except Exception as exc:
        print(f"\n  PREFLIGHT FAIL  configuration: {exc}", file=sys.stderr)
        raise SystemExit(2)

    provider = getattr(cfg, "llm_provider", "anthropic")
    key = (getattr(cfg, f"{provider}_api_key", "") or "")
    # Enough to recognise WHICH key is in play without printing one.
    fp = f"{key[:11]}…{key[-4:]} ({len(key)} chars)" if len(key) > 20 else "(short)"
    print(f"  model auth      : provider={provider} key={fp}")

    if provider != "anthropic":
        print("                    not verified — only the anthropic route is checked")
        return
    try:
        import anthropic
    except ImportError:
        print("                    not verified — the anthropic package is absent")
        return
    try:
        anthropic.Anthropic(api_key=key).models.list(limit=1)
        print("                    verified — the key authenticates")
    except Exception as exc:
        name = type(exc).__name__
        if "Authentication" in name or "PermissionDenied" in name or "401" in str(exc):
            print(f"\n  PREFLIGHT FAIL  ANTHROPIC_API_KEY is set but does not "
                  f"authenticate ({name}).\n"
                  f"                  An explicit key overrides any other route, so a "
                  f"stale one in the\n"
                  f"                  environment or .env wins silently and 401s at the "
                  f"first model call.\n"
                  f"                  Fix or unset it, or pass --skip-auth-check to "
                  f"proceed anyway.", file=sys.stderr)
            raise SystemExit(2)
        # Anything else -- a network blip, a proxy -- is not a credential verdict.
        print(f"                    not verified — {name} (not an auth failure)")


def preflight(urls_path: Path, out: Path, resume: bool, force: bool) -> list[str]:
    """Everything that can be known before a single page is fetched.

    Checked here so an unreadable URL file or a missing tool fails in a second
    rather than after a capture pass. Returns the URL list.
    """
    problems: list[str] = []
    for tool in (HARNESS, ANALYZER, FEEDER):
        if not tool.exists():
            problems.append(f"missing tool: {tool.relative_to(ROOT)}")
    if not urls_path.exists():
        problems.append(f"missing URL file: {urls_path}")
    if problems:
        for p in problems:
            print(f"  PREFLIGHT FAIL  {p}", file=sys.stderr)
        raise SystemExit(2)

    urls = read_urls(urls_path)
    if not urls:
        print(f"  PREFLIGHT FAIL  {urls_path} contains no URLs", file=sys.stderr)
        raise SystemExit(2)

    bad = [u for u in urls if not u.lower().startswith(("http://", "https://"))]
    if bad:
        print(f"  PREFLIGHT FAIL  {len(bad)} line(s) are not http(s) URLs, first: "
              f"{bad[0]!r}", file=sys.stderr)
        raise SystemExit(2)

    db = out / "collection.db"
    if db.exists() and not force:
        print(f"  PREFLIGHT FAIL  {db} exists — this run directory already holds a "
              f"completed run.\n"
              f"                  Use a fresh --out, or --force to archive the "
              f"previous outputs and re-run.", file=sys.stderr)
        raise SystemExit(2)
    if resume and not (out / "pages").is_dir():
        print(f"  PREFLIGHT FAIL  --resume needs {out / 'pages'} to exist",
              file=sys.stderr)
        raise SystemExit(2)

    dupes = len(urls) - len(set(urls))
    print(f"  urls            : {len(urls)}"
          + (f"  ({dupes} duplicate line(s) — the harness will capture each once, "
             f"they share a slug)" if dupes else ""))
    return urls


def archive_previous(out: Path) -> Path | None:
    """Move a completed run's outputs aside. Never deletes.

    `pages/` is deliberately left where it is: the captures are the expensive
    part, they are keyed by URL slug, and the harness overwrites its own
    capture directories in place.
    """
    movable = [p for p in (out / "collection.db", out / "manifest.json",
                           out / "ma_review.csv", out / "funding_review.csv",
                           out / "relevancy_rejections.csv", out / "diagnostics",
                           out / "capture_report.json", out / "failed_urls.txt")
               if p.exists()]
    if not movable:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = out / f"superseded-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for p in movable:
        shutil.move(str(p), str(dest / p.name))
    print(f"  archived        : {len(movable)} prior output(s) -> {dest}")
    return dest


def run_harness(urls_path: Path, pages: Path, passthrough: list[str]) -> int:
    """Capture. The harness owns every fetch decision; this only places the output.

    Its exit code is returned, not obeyed -- see the module docstring.
    """
    cmd = [sys.executable, str(HARNESS), "--urls", str(urls_path),
           "--out", str(pages), *passthrough]
    print(f"\n  $ {' '.join(cmd)}\n")
    # Harness output is per-page JSON on stdout; let it stream so a long capture
    # shows progress rather than going quiet.
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def run_analyzer(pages: Path, report_json: Path) -> int:
    cmd = [sys.executable, str(ANALYZER), str(pages), "--json", str(report_json)]
    print(f"\n  $ {' '.join(cmd)}\n")
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def write_failures(feeder, pages: Path, out: Path) -> tuple[int, int, Path | None]:
    """Partition the captures using the FEEDER's gate, and leave a re-feedable list.

    Returns (healthy, quarantined, failed_urls_path). The file is plain URLs,
    one per line, so it can be handed straight back to --urls without editing.
    """
    healthy, quarantined = feeder.load_url_captures(str(pages))
    path = None
    if quarantined:
        path = out / "failed_urls.txt"
        lines = ["# URLs whose capture was blocked, suspect or unreadable.",
                 "# Re-feedable: python scripts/run_acceptance.py --urls "
                 "<this file> --out <new dir>", ""]
        for q in quarantined:
            url = q.get("url")
            reason = (q.get("reason") or "").replace("\n", " ")
            lines.append(f"# {q.get('capture_dir')}: {reason}" if not url
                         else f"{url}    # {reason}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(healthy), len(quarantined), path


def run_feeder(pages: Path, out: Path) -> int:
    """URL-only mode: --pl-ids / --pl-tsv are omitted as a pair."""
    cmd = [sys.executable, str(FEEDER), "--pages", str(pages), "--out-dir", str(out)]
    print(f"\n  $ {' '.join(cmd)}\n")
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Anything after -- goes to tools/page_harness.py unchanged, e.g.\n"
               "  run_acceptance.py --urls u.txt --out out/x -- --profile ~/.pw "
               "--disable-http2")
    ap.add_argument("--urls", required=True,
                    help="URL source: a .txt file (one URL per line) or an .xlsx "
                         "workbook whose URL column is read in row order")
    ap.add_argument("--out", required=True, help="run directory; everything lands here")
    # Harness controls, forwarded rather than redefined. The defaults are the
    # ones acceptance is actually run with -- one worker, a second between
    # fetches -- and they are the harness's own defaults, not a new policy.
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--profile", help="Chromium profile dir; enables the Playwright tier")
    ap.add_argument("--disable-http2", action="store_true",
                    help="harness: launch Playwright HTTP/1.1-only (needs --profile)")
    ap.add_argument("--resume", action="store_true",
                    help="reuse an existing <out>/pages and skip capture")
    ap.add_argument("--force", action="store_true",
                    help="archive a completed run's outputs and re-run")
    ap.add_argument("--capture-only", action="store_true",
                    help="stop after capture and diagnosis; run no stages")
    ap.add_argument("--skip-auth-check", action="store_true",
                    help="do not verify model credentials before capturing")
    ap.add_argument("harness_args", nargs="*",
                    help="extra harness arguments after --")
    args = ap.parse_args(argv)

    urls_path = Path(args.urls).resolve()
    out = Path(args.out).resolve()
    pages = out / "pages"

    print("=" * 74)
    print("ACCEPTANCE RUN")
    print("=" * 74)
    print(f"  url file        : {urls_path}")
    print(f"  run directory   : {out}")
    urls = preflight(urls_path, out, args.resume, args.force)

    preflight_model_auth(args.skip_auth_check or args.capture_only)

    out.mkdir(parents=True, exist_ok=True)
    if args.force:
        archive_previous(out)
    pages.mkdir(parents=True, exist_ok=True)

    # The harness takes --urls FILE and reads it itself. A workbook is normalized
    # into the plain list it already understands rather than teaching it a second
    # input format -- the harness stays untouched, and the file doubles as the
    # record of exactly which URLs this run was given.
    if urls_path.suffix.lower() in (".xlsx", ".xlsm"):
        resolved = out / "urls_resolved.txt"
        resolved.write_text(
            f"# resolved from {urls_path.name}, workbook row order preserved\n"
            + "\n".join(urls) + "\n", encoding="utf-8")
        print(f"  normalized to   : {resolved}")
        harness_urls = resolved
    else:
        harness_urls = urls_path

    passthrough = ["--workers", str(args.workers), "--delay", str(args.delay),
                   "--timeout", str(args.timeout)]
    if args.profile:
        passthrough += ["--profile", args.profile]
    if args.disable_http2:
        passthrough.append("--disable-http2")
    passthrough += list(args.harness_args)

    if args.resume:
        print(f"\n  --resume: reusing captures in {pages}, skipping the harness")
        harness_rc = None
    else:
        harness_rc = run_harness(harness_urls, pages, passthrough)
        # Exit 1 means "some page failed", which is expected on a real corpus.
        print(f"\n  harness exit    : {harness_rc}"
              + ("  (some captures failed — continuing with the healthy ones)"
                 if harness_rc else ""))

    report_json = out / "capture_report.json"
    run_analyzer(pages, report_json)

    feeder = _load_feeder()
    healthy, quarantined, failed_path = write_failures(feeder, pages, out)
    print(f"  captures healthy: {healthy}")
    print(f"  quarantined     : {quarantined}"
          + (f"  -> {failed_path}" if failed_path else ""))

    if not healthy:
        print("\n  STOP: no healthy captures — nothing to validate.", file=sys.stderr)
        return 3
    if args.capture_only:
        print("\n  --capture-only: stopping before the pipeline.")
        _report_paths(out, pages, report_json, failed_path, feeder_ran=False)
        return 0

    feeder_rc = run_feeder(pages, out)
    _report_paths(out, pages, report_json, failed_path, feeder_ran=True)
    return feeder_rc


def _report_paths(out: Path, pages: Path, report_json: Path,
                  failed_path: Path | None, *, feeder_ran: bool) -> None:
    print("\n" + "=" * 74)
    print("  OUTPUTS")
    print("=" * 74)
    if feeder_ran:
        print(f"  M&A review      : {out / 'ma_review.csv'}")
        print(f"  Funding review  : {out / 'funding_review.csv'}")
        print(f"  Rejections      : {out / 'relevancy_rejections.csv'}")
        print(f"  Database        : {out / 'collection.db'}")
        print(f"  Diagnostics     : {out / 'diagnostics'}/")
        print(f"  Manifest        : {out / 'manifest.json'}")
    print(f"  Captures        : {pages}/")
    print(f"  Capture report  : {report_json}")
    print(f"  Failed/quarantined: {failed_path if failed_path else '(none)'}")


if __name__ == "__main__":
    raise SystemExit(main())
