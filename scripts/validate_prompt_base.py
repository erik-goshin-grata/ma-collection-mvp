"""
Standalone validation script for prompts/base.py.

Validates end-to-end:
  1. load_prompt_file() correctly parses prompts/relevancy_filter.md
  2. call_prompt() reaches the configured relevancy model and returns a response
  3. The returned dict has the expected relevancy_filter shape

Uses an in-memory SQLite DB — no writes to the pipeline database.

Usage:
    python3 scripts/validate_prompt_base.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from config import get_config
from prompts.base import call_prompt, load_prompt_file, register_prompt_version


_PROMPT_NAME = "relevancy_filter"
_PROMPT_VERSION = "relevancy_filter:0.1"

# A clearly in-scope PR — acquisition announcement, definitive agreement.
_DUMMY_TITLE = "Acme Corp to Acquire Beta Industries for $500 Million in Cash"
_DUMMY_BODY = (
    "Acme Corp (NASDAQ: ACME), a leading provider of industrial components, today "
    "announced it has entered into a definitive agreement to acquire Beta Industries, "
    "a privately held manufacturer of specialty precision parts, for $500 million in "
    "cash. The transaction is expected to close in Q3 2026, subject to customary "
    "regulatory approvals and shareholder vote. The acquisition will expand Acme's "
    "product portfolio and accelerate its entry into the European market."
)

_EXPECTED_KEYS = {"classification", "reason_code", "model_confidence", "notes", "prompt_version"}


def _make_minimal_db() -> sqlite3.Connection:
    """Create an in-memory DB with just the tables call_prompt needs."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE extraction_failure_log (
            failure_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            source_raw_id  INTEGER,
            extraction_id  INTEGER,
            stage          TEXT NOT NULL,
            failure_type   TEXT,
            raw_response   TEXT,
            error_message  TEXT,
            prompt_version TEXT,
            run_id         TEXT,
            created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE prompt_version (
            prompt_name      TEXT NOT NULL,
            version          TEXT NOT NULL,
            prompt_text_hash TEXT,
            first_used_at    TEXT NOT NULL,
            notes            TEXT,
            PRIMARY KEY (prompt_name, version)
        );
        """
    )
    return conn


def main() -> None:
    print("=" * 60)
    print("validate_prompt_base — relevancy_filter via configured LLM provider")
    print("=" * 60)

    # --- 1. Config ---
    try:
        cfg = get_config()
    except Exception as exc:
        print(f"FAIL — config error: {exc}")
        sys.exit(1)
    print(f"Config loaded. provider={cfg.llm_provider} haiku_alias={cfg.haiku_model}")

    # --- 2. load_prompt_file ---
    print(f"\nLoading prompts/{_PROMPT_NAME}.md ...")
    try:
        prompt = load_prompt_file(_PROMPT_NAME)
    except Exception as exc:
        print(f"FAIL — load_prompt_file: {exc}")
        sys.exit(1)

    print(f"  file_hash (first 16 chars): {prompt['file_hash'][:16]}")
    print(f"  system prompt length:       {len(prompt['system'])} chars")
    print(f"  user_template length:       {len(prompt['user_template'])} chars")
    print(f"  user_template preview:      {prompt['user_template'][:60]!r}")

    # --- 3. Register prompt version ---
    conn = _make_minimal_db()
    register_prompt_version(conn, _PROMPT_NAME, "0.1", prompt["file_hash"])
    row = conn.execute(
        "SELECT * FROM prompt_version WHERE prompt_name=?", (_PROMPT_NAME,)
    ).fetchone()
    print(f"\nprompt_version row: name={row['prompt_name']} version={row['version']} "
          f"hash={row['prompt_text_hash'][:16]}")

    # --- 4. call_prompt ---
    user_prompt = prompt["user_template"].format(
        title=_DUMMY_TITLE,
        clean_text=_DUMMY_BODY,
    )
    print("\ncall_prompt → model alias=haiku ...")
    try:
        result = call_prompt(
            prompt_name=_PROMPT_NAME,
            prompt_version=_PROMPT_VERSION,
            user_prompt=user_prompt,
            system_prompt=prompt["system"],
            model="haiku",
            temperature=0.0,
            max_tokens=256,
            cfg=cfg,
            conn=conn,
            run_id="validate_run",
        )
    except Exception as exc:
        print(f"FAIL — call_prompt: {exc}")
        sys.exit(1)

    # --- 5. Validate response shape ---
    print("\nParsed response:")
    for k, v in result.items():
        print(f"  {k}: {v!r}")

    missing = _EXPECTED_KEYS - result.keys()
    if missing:
        print(f"\nWARNING — missing expected keys: {missing}")
    else:
        print(f"\nAll expected keys present: {sorted(_EXPECTED_KEYS)}")

    if result.get("classification") == "RELEVANT":
        print("classification=RELEVANT ✓  (correct for acquisition announcement)")
    else:
        print(f"WARNING — expected RELEVANT, got {result.get('classification')!r}")

    print("\nValidation complete.")


if __name__ == "__main__":
    main()
