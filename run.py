"""
M&A Collection MVP — pipeline entry point.

Parses a mode flag, generates a run_id, initialises the database, and
dispatches to the appropriate pipeline stages in sequence. All stages are
idempotent; the pipeline can be interrupted and resumed via --mode=resume.

Usage:
    python run.py --mode=full                                   # end-to-end run
    python run.py --mode=resume                                 # default; resume from last state
    python run.py --mode=scrape                                 # stage 1 only
    python run.py --mode=extract                                # stages 2–7
    python run.py --mode=aggregate                              # stages 8–9
    python run.py --mode=sec-documents                          # stage 10 only (expanded SEC filing fetch)
    python run.py --mode=agreement-extract                      # stage 11 only (agreement extraction)
    python run.py --mode=agreement-rerun                        # stage 11 re-run on all unextracted docs
    python run.py --mode=generate                               # stages 12–13
    python run.py --mode=export                                 # stage 14
    python run.py --mode=rerun-prompt --prompt=<name> --version=<ver>

Spec references: specs/pipeline.md §4 (run modes), §6 (error posture), §7 (configuration)
"""

from __future__ import annotations

import argparse
import sys
import time
import types
from datetime import datetime, timezone
from typing import Any

from config import ConfigurationError, get_config
from db import get_connection, init_db
from logger import get_logger, insert_run_log, update_run_log

import stages.scrape_pr_newswire as _stage_1
import stages.relevancy_filter as _stage_2
import stages.deal_type_classify as _stage_3
import stages.high_confidence_extract as _stage_4
import stages.sec_trigger_detect as _stage_5
import stages.sec_enrich as _stage_6
import stages.low_confidence_extract as _stage_7
import stages.entity_cluster as _stage_8
import stages.aggregate as _stage_9
import stages.sec_documents as _stage_10
import stages.agreement_extract as _stage_11
import stages.summarize as _stage_12
import stages.rationale_tag as _stage_13
import stages.export as _stage_14

# All 14 stages in pipeline order.
_ALL_STAGES: list[types.ModuleType] = [
    _stage_1, _stage_2, _stage_3, _stage_4,
    _stage_5, _stage_6, _stage_7, _stage_8,
    _stage_9, _stage_10, _stage_11, _stage_12, _stage_13, _stage_14,
]

_EXTRACTION_STAGES = [
    _stage_2, _stage_3, _stage_4,
    _stage_5, _stage_6,
    # Stage 6 can queue SEC attached sources with inherited transaction context.
    # Run HC extraction again so those sources reach Stage 7 in the same run.
    _stage_4,
    _stage_7,
]

# Stages executed per mode, per pipeline.md §4.
_MODE_STAGES: dict[str, list[types.ModuleType]] = {
    "full":               _ALL_STAGES,
    "resume":             _EXTRACTION_STAGES + [_stage_8, _stage_9, _stage_10, _stage_11, _stage_12, _stage_13, _stage_14],
    "scrape":             [_stage_1],
    "extract":            _EXTRACTION_STAGES,
    "aggregate":          [_stage_8, _stage_9],
    "sec-documents":      [_stage_10],
    "agreement-extract":  [_stage_11],
    "agreement-rerun":    [_stage_11],    # same stage; per-doc gate picks up unextracted docs
    "generate":           [_stage_12, _stage_13],
    "export":             [_stage_14],
}


def _generate_run_id(prefix: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_run_{ts}" if prefix else f"run_{ts}"


def _format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def _print_summary(summary: dict[str, Any], duration: float, run_id: str) -> None:
    """Print the run summary table matching the format in pipeline.md §6."""
    fetched = summary.get("sources_fetched", 0)
    relevant = summary.get("relevant", 0)
    extracted = summary.get("extracted", 0)
    clusters = summary.get("clusters", 0)
    merges = summary.get("merged_duplicates", 0)
    failures = summary.get("failures", 0)
    failure_log = summary.get("failure_log", "")
    export_path = summary.get("export_path", "")

    print("\nPipeline run complete.")
    print(f"  Run ID:               {run_id}")
    print(f"  Duration:             {_format_duration(duration)}")
    print(f"  Sources fetched:      {fetched}")
    print(f"  Relevant:             {relevant} / {fetched}")
    print(f"  Extracted:            {extracted} / {relevant}")
    print(
        f"  Clustered:            {clusters} clusters from {extracted} extractions"
        f" ({merges} merged duplicates)"
    )
    print(f"  Transactions created: {summary.get('transactions_created', 0)}")
    print(f"  Summaries generated:  {summary.get('summaries_generated', 0)}")
    print(f"  Rationales tagged:    {summary.get('rationales_tagged', 0)}")
    if failures:
        print(f"  Failures:             {failures} ({failure_log})")
    else:
        print(f"  Failures:             0")
    if export_path:
        print(f"  Export:               {export_path}")


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge a stage result dict into the run-level summary.

    Integer values are summed; string values (e.g. export_path) are replaced.
    """
    for key, val in update.items():
        if isinstance(val, int):
            base[key] = base.get(key, 0) + val
        else:
            base[key] = val
    return base


def _run_rerun_prompt(
    conn: Any,
    cfg: Any,
    run_id: str,
    prompt: str,
    version: str,
    log: Any,
) -> dict[str, Any]:
    """Reset is_current flags for the given prompt and re-run the relevant stage."""
    _RERUN_MAP = {
        "deal_summary": ("summary", "is_current", _stage_12),
        "strategic_rationale": ("rationale_tag", "is_current", _stage_13),
    }
    if prompt not in _RERUN_MAP:
        log.warning(
            "rerun-prompt: prompt=%s is not supported — no work performed", prompt
        )
        return {}

    table, flag_col, stage_mod = _RERUN_MAP[prompt]
    rows_reset = conn.execute(
        f"UPDATE {table} SET {flag_col} = 0 WHERE {flag_col} = 1"
    ).rowcount
    conn.commit()
    log.info(
        "rerun-prompt: reset %d %s rows to is_current=0  prompt=%s version=%s",
        rows_reset, table, prompt, version,
    )
    return _run_stage_list([stage_mod], conn, cfg, run_id, log)


def _run_stage_list(
    stage_list: list[types.ModuleType],
    conn: Any,
    cfg: Any,
    run_id: str,
    log: Any,
) -> dict[str, Any]:
    """Execute each stage in order, merging their result dicts.

    Any exception propagated by a stage is treated as an infrastructure-level
    failure and re-raised to halt the run. Row-level failures are handled
    inside each stage and do not propagate.
    """
    summary: dict[str, Any] = {}
    for stage_mod in stage_list:
        stage_name = stage_mod.__name__.split(".")[-1]
        log.info("Running stage: %s", stage_name)
        result = stage_mod.run(conn=conn, cfg=cfg, run_id=run_id)
        _merge(summary, result)
        log.info("Stage %s complete: %s", stage_name, result)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="M&A Collection MVP pipeline",
    )
    parser.add_argument(
        "--mode",
        default="resume",
        choices=sorted(_MODE_STAGES.keys()) + ["rerun-prompt"],
        help="Pipeline run mode. Default: resume.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        metavar="PROMPT_NAME",
        help="Prompt name for --mode=rerun-prompt (e.g. deal_type_classifier).",
    )
    parser.add_argument(
        "--version",
        default=None,
        metavar="VERSION",
        help="New prompt version for --mode=rerun-prompt (e.g. 0.3).",
    )
    args = parser.parse_args()

    if args.mode == "rerun-prompt" and not (args.prompt and args.version):
        parser.error("--mode=rerun-prompt requires both --prompt and --version")

    # Load config — fails loudly on missing required variables.
    try:
        cfg = get_config()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run_id = _generate_run_id(cfg.run_id_prefix)
    log = get_logger("pipeline", run_id, level=cfg.log_level)
    log.info("Starting run %s  mode=%s", run_id, args.mode)

    try:
        init_db(cfg.db_path)
        conn = get_connection(cfg.db_path)
    except Exception as exc:
        log.error("Database initialisation failed: %s", exc, exc_info=True)
        sys.exit(1)

    insert_run_log(conn, run_id, args.mode)
    start = time.monotonic()

    try:
        if args.mode == "rerun-prompt":
            summary = _run_rerun_prompt(conn, cfg, run_id, args.prompt, args.version, log)
        else:
            stage_list = _MODE_STAGES[args.mode]
            summary = _run_stage_list(stage_list, conn, cfg, run_id, log)

        duration = time.monotonic() - start
        update_run_log(conn, run_id, "COMPLETED", summary_json=summary)
        _print_summary(summary, duration, run_id)

    except Exception as exc:
        duration = time.monotonic() - start
        try:
            update_run_log(conn, run_id, "FAILED", error_message=str(exc))
        except Exception:
            pass  # Don't mask the original error if the DB write also fails.
        log.error(
            "Run %s FAILED after %s: %s",
            run_id, _format_duration(duration), exc,
            exc_info=True,
        )
        sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
