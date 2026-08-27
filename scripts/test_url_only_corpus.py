#!/usr/bin/env python3
"""The Collection feeder accepts a URL-only corpus.

WHAT WENT WRONG

`--pl-ids` and `--pl-tsv` were both `required=True`, because the corpus the feeder
was built for was mixed: captured Collection URLs plus PL event sources resolved
against a PL export. A later corpus is deliberately URL-only, and the feeder
refused to start without a PL half that does not exist.

WHAT CHANGED, AND WHAT DID NOT

The two arguments become optional **as a pair**. Neither given is a URL-only
corpus: zero PL sources, zero unresolved, a valid run. Both given is byte-for-byte
the behaviour it has always had -- the resolver's body is untouched, and this file
pins that by driving a real mixed corpus through it.

One without the other is refused. That case is not a smaller corpus, it is a
mistake that looks like a successful run: ids with no export resolve nothing and
report every id unresolved, and an export with no ids contributes nothing at all.
Both would print a plausible summary. The check sits on the command line so it
fires before the output directory is created and before any capture is read, and
the resolver carries the same invariant so a direct caller cannot slip past it.

WHAT THIS IS NOT

Harness functionality, not Product behaviour. No stage, prompt, schema or
extraction logic is touched, and the URL capture path -- the harness gate
`ok and not suspect`, the seeding, the review projection -- is asserted unchanged.

Run from project root:
    python scripts/test_url_only_corpus.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _load_feeder():
    path = ROOT / "scripts" / "run_collection_validation.py"
    spec = importlib.util.spec_from_file_location("_rcv_urlonly", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rcv_urlonly"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_capture(pages: Path, name: str, url: str, *, ok=True, suspect=False,
                   blocked=False, text="Body text long enough to be a real page.") -> None:
    d = pages / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({
        "url": url, "ok": ok, "suspect": suspect, "blocked": blocked,
        "via": "curl_cffi:chrome", "title": f"title {name}",
        "published": "2026-08-26", "candidates": {"trafilatura": len(text)},
    }), encoding="utf-8")
    (d / "page.txt").write_text(text, encoding="utf-8")
    (d / "page.html").write_text(f"<html><body>{text}</body></html>", encoding="utf-8")


def _write_pl(tmp: Path) -> tuple[str, str]:
    ids = tmp / "pl_ids.txt"
    ids.write_text("# a comment line\nEV-1\nEV-2\nEV-MISSING\n", encoding="utf-8")
    tsv = tmp / "pl_export.tsv"
    cols = ["event_id", "source_url", "source_title", "source_published_at",
            "source_body_lite", "category"]
    rows = [
        ["EV-1", "https://pl.example/1", "PL One", "2026-08-01", "PL one body", "M&A"],
        ["EV-2", "https://pl.example/2", "PL Two", "2026-08-02", "PL two body", "M&A"],
        ["EV-OTHER", "https://pl.example/9", "Other", "2026-08-03", "other", "M&A"],
    ]
    tsv.write_text("\t".join(cols) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n",
                   encoding="utf-8")
    return str(ids), str(tsv)


# ---------------------------------------------------------------------------
# 1. The resolver
# ---------------------------------------------------------------------------

def test_resolver(fv) -> None:
    tmp = Path(tempfile.mkdtemp())
    ids, tsv = _write_pl(tmp)

    print("\nNeither argument — a URL-only corpus resolves to no PL sources:")
    # Guarded. On a pre-change tree the resolver opens None and raises TypeError,
    # which would abort the run here and leave every control below unproven -- that
    # reads as "nothing else is broken" when in fact nothing else was tested.
    def _resolve(a, b):
        try:
            return fv.load_pl_sources(a, b)
        except Exception as exc:                                     # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"
    check("both None", _resolve(None, None), ([], []))
    check("both empty strings", _resolve("", ""), ([], []))

    print("\nBoth arguments — unchanged, and still resolving in the supplied order:")
    resolved = _resolve(ids, tsv)
    check("mixed corpus still resolves", isinstance(resolved, tuple), True)
    sources, unresolved = resolved if isinstance(resolved, tuple) else ([], [])
    check("two resolved", [s["event_id"] for s in sources], ["EV-1", "EV-2"])
    check("one unresolved", unresolved, ["EV-MISSING"])
    check("kind is PL_EVENT", {s["kind"] for s in sources}, {"PL_EVENT"})
    check("body carried", sources[0]["text"] if sources else None, "PL one body")
    check("no HTML invented", [s["html"] for s in sources], [None, None])
    check("export name recorded",
          sources[0]["pl_export"] if sources else None, "pl_export.tsv")
    check("a comment line is not an id", "# a comment line" in unresolved, False)
    check("an export row nobody asked for is ignored",
          any(s["event_id"] == "EV-OTHER" for s in sources), False)

    print("\nOne without the other — refused, at the resolver too:")
    for label, a, b in (("ids without export", ids, None),
                        ("export without ids", None, tsv)):
        try:
            fv.load_pl_sources(a, b)
            check(f"{label} raises", False, True)
        except ValueError as exc:
            check(f"{label} raises ValueError", "together, or neither" in str(exc), True)
        except Exception as exc:                                     # noqa: BLE001
            check(f"{label} raises ValueError, not {type(exc).__name__}", False, True)


# ---------------------------------------------------------------------------
# 2. The command line
# ---------------------------------------------------------------------------

def _run_main(fv, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    argv_backup = sys.argv
    sys.argv = ["run_collection_validation.py"] + argv
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = fv.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = argv_backup
    return code, out.getvalue(), err.getvalue()


def test_cli(fv) -> None:
    tmp = Path(tempfile.mkdtemp())
    pages = tmp / "pages"
    _write_capture(pages, "a_healthy", "https://ex.com/a")
    _write_capture(pages, "b_healthy", "https://ex.com/b")
    _write_capture(pages, "c_suspect", "https://ex.com/c", suspect=True)
    ids, tsv = _write_pl(tmp)

    print("\nURL-only dry run — valid, and the PL line reports zero:")
    code, out, err = _run_main(fv, ["--pages", str(pages),
                                    "--out-dir", str(tmp / "out_urlonly"), "--dry-run"])
    check("exit 0", code, 0)
    check("PL sources resolved  : 0   unresolved: 0",
          "PL sources resolved  : 0   unresolved: 0" in out, True)
    check("captures still gated — 2 healthy", "URL captures healthy : 2" in out, True)
    check("and 1 quarantined", "URL captures quarantined: 1" in out, True)
    check("seeded the healthy two only", "seeded 2 source_raw rows" in out, True)
    check("dry run stopped before any stage", "DRY RUN" in out, True)

    print("\nMixed dry run — unchanged: the two PL sources join the two captures:")
    code, out, err = _run_main(fv, ["--pages", str(pages), "--pl-ids", ids,
                                    "--pl-tsv", tsv,
                                    "--out-dir", str(tmp / "out_mixed"), "--dry-run"])
    check("exit 0", code, 0)
    check("PL sources resolved  : 2   unresolved: 1",
          "PL sources resolved  : 2   unresolved: 1" in out, True)
    check("the unresolved id is named", "UNRESOLVED  EV-MISSING" in out, True)
    check("sources to seed = 2 captures + 2 PL", "sources to seed      : 4" in out, True)
    check("seeded four", "seeded 4 source_raw rows" in out, True)

    print("\nHalf-specified — refused on the command line, clearly:")
    for label, extra in (("--pl-ids alone", ["--pl-ids", ids]),
                         ("--pl-tsv alone", ["--pl-tsv", tsv])):
        outdir = tmp / f"out_half_{len(extra)}_{label.split()[0].strip('-')}"
        code, out, err = _run_main(fv, ["--pages", str(pages),
                                        "--out-dir", str(outdir), "--dry-run"] + extra)
        check(f"{label}: argparse exit 2", code, 2)
        check(f"{label}: says they go together",
              "must be given together, or neither" in err, True)
        check(f"{label}: names the URL-only case", "URL-only corpus supplies neither" in err,
              True)
        # The check runs before any side effect: nothing was created on disk.
        check(f"{label}: no output directory created", outdir.exists(), False)

    print("\n--pages is still required:")
    code, out, err = _run_main(fv, ["--dry-run"])
    check("missing --pages exits 2", code, 2)
    check("and argparse says so", "--pages" in err, True)


# ---------------------------------------------------------------------------
# 3. Nothing else moved
# ---------------------------------------------------------------------------

def test_untouched(fv) -> None:
    print("\nThe URL capture path and the review contract are untouched:")
    src = (ROOT / "scripts" / "run_collection_validation.py").read_text(encoding="utf-8")
    check("harness gate unchanged", "if not (ok and not suspect):" in src, True)
    check("empty page.txt still quarantined",
          "gate passed but page.txt is empty" in src, True)
    check("seeding still at FETCHED — the SQL literal",
          "'FETCHED', ?, ?)" in src, True)
    check("and still with no relevancy pre-seed",
          "source_status=FETCHED, no relevancy pre-seed" in src, True)
    check("still exactly one INSERT", len(re.findall(r"INSERT INTO", src)), 1)
    check("and it is into source_raw", "INSERT INTO source_raw" in src, True)
    check("still the same eight stages", len(fv.PIPELINE), 8)
    check("review sheet version unchanged", fv._REVIEW_SHEET_VERSION, "1.1")
    check("M&A sheet still 83 columns", len(fv._MA_COLS), 83)
    check("funding sheet still 45 columns", len(fv._FUNDING_COLS), 45)
    check("never writes to transaction_record",
          bool(re.search(r"(UPDATE|DELETE\s+FROM)\s+transaction_record", src)), False)

    print("\nNo Product surface was touched by this change:")
    import subprocess
    changed = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    offending = [f for f in changed
                 if f.startswith(("stages/", "prompts/", "schema/", "lib/"))]
    check("no stages/prompts/schema/lib file is modified", offending, [])


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    fv = _load_feeder()
    test_resolver(fv)
    test_cli(fv)
    test_untouched(fv)
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — URL-only corpora run; mixed corpora unchanged; half-specified refused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
