#!/usr/bin/env python3
"""Replay tasks from tasks.csv against a running local server.

Usage:
    # Replay all tasks (skips file-dependent tasks like PDFs/CSVs)
    python scripts/replay.py

    # Filter by task type
    python scripts/replay.py --filter payroll
    python scripts/replay.py --filter create_customer

    # Run a single task by row number (1-indexed, skipping header)
    python scripts/replay.py --row 82

    # Custom server/credentials
    python scripts/replay.py --url http://localhost:8000 --base-url https://xxx.tripletex.dev/v2 --token xxx

    # Dry run (just show which tasks would run)
    python scripts/replay.py --dry-run
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import httpx

TASKS_CSV = Path(__file__).parent.parent / "docs" / "tasks.csv"

# Defaults — update these or pass via CLI
DEFAULT_SERVER = "http://localhost:8000"
DEFAULT_BASE_URL = "https://kkpqfuj-amager.tripletex.dev/v2"
DEFAULT_TOKEN = "eyJ0b2tlbklkIjoyMTQ3Njg2NDA1LCJ0b2tlbiI6IjJhYjczNTIyLTQzZWUtNDY5OC05ZDI3LTQzYzFhNDM2M2UxMiJ9"


def load_tasks() -> list[dict]:
    tasks = []
    with open(TASKS_CSV) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # row 1 is header
            prompt = (row.get("prompt") or "").strip()
            task_type = (row.get("task_type") or "").strip()
            if prompt and task_type:
                row["_row"] = i
                tasks.append(row)
    return tasks


def replay_task(task: dict, server_url: str, base_url: str, token: str) -> dict:
    """Send a task to the server and return the response."""
    payload = {
        "prompt": task["prompt"],
        "files": [],
        "tripletex_credentials": {
            "base_url": base_url,
            "session_token": token,
        },
    }

    start = time.time()
    resp = httpx.post(
        f"{server_url}/solve",
        json=payload,
        timeout=180.0,
    )
    elapsed = time.time() - start

    result = resp.json()
    result["_elapsed"] = round(elapsed, 1)
    return result


def has_file_dependency(task: dict) -> bool:
    """Check if a task requires file attachments (PDFs, CSVs)."""
    notes = (task.get("notes") or "").lower()
    prompt = (task.get("prompt") or "").lower()
    task_type = (task.get("task_type") or "").lower()

    file_indicators = ["pdf", "csv", "vedlagt", "anexo", "adjunto", "attached", "beigefuegte", "see attached", "kvittering", "recibo", "quittung", "reçu", "tilbudsbrev", "contrato"]
    return any(ind in prompt or ind in notes for ind in file_indicators)


def main():
    parser = argparse.ArgumentParser(description="Replay tasks.csv against local server")
    parser.add_argument("--url", default=DEFAULT_SERVER, help="Server URL")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Tripletex base URL")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Session token")
    parser.add_argument("--filter", help="Filter by task_type substring")
    parser.add_argument("--row", type=int, help="Run single row by number")
    parser.add_argument("--dry-run", action="store_true", help="Just show tasks, don't run")
    parser.add_argument("--include-files", action="store_true", help="Include tasks that need file attachments")
    parser.add_argument("--status", help="Filter by original status (success/partial/fail)")
    args = parser.parse_args()

    tasks = load_tasks()

    # Apply filters
    if args.row:
        tasks = [t for t in tasks if t["_row"] == args.row]
        if not tasks:
            print(f"No task found at row {args.row}")
            sys.exit(1)
    if args.filter:
        tasks = [t for t in tasks if args.filter.lower() in t.get("task_type", "").lower()]
    if args.status:
        tasks = [t for t in tasks if t.get("status", "").lower() == args.status.lower()]
    if not args.include_files:
        before = len(tasks)
        tasks = [t for t in tasks if not has_file_dependency(t)]
        skipped = before - len(tasks)
        if skipped:
            print(f"(Skipped {skipped} file-dependent tasks. Use --include-files to include them.)\n")

    if not tasks:
        print("No matching tasks found.")
        sys.exit(0)

    print(f"{'='*70}")
    print(f"REPLAY: {len(tasks)} task(s) against {args.url}")
    print(f"Tripletex: {args.base_url}")
    print(f"{'='*70}\n")

    if args.dry_run:
        for t in tasks:
            file_flag = " [FILES]" if has_file_dependency(t) else ""
            print(f"  Row {t['_row']:3d} | {t.get('status', '?'):7s} | {t.get('task_type', '?'):40s} | {t['prompt'][:60]}...{file_flag}")
        print(f"\n{len(tasks)} task(s) would be replayed.")
        return

    results = {"success": 0, "error": 0, "total": len(tasks)}

    for i, task in enumerate(tasks, 1):
        row = task["_row"]
        task_type = task.get("task_type", "?")
        old_status = task.get("status", "?")
        prompt_short = task["prompt"][:70]

        print(f"[{i}/{len(tasks)}] Row {row} | {task_type}")
        print(f"  Prompt: {prompt_short}...")
        print(f"  Original: {old_status} (checks: {task.get('checks_passed', '?')}/{task.get('checks_failed', '?')})")

        try:
            result = replay_task(task, args.url, args.base_url, args.token)
            status = result.get("status", "?")
            api_calls = result.get("api_calls", "?")
            api_errors = result.get("api_errors", "?")
            elapsed = result.get("_elapsed", "?")

            print(f"  Result: {status} | {elapsed}s | API calls: {api_calls} | errors: {api_errors}")

            if status == "completed":
                results["success"] += 1
            else:
                results["error"] += 1
                print(f"  Error: {json.dumps(result.get('result', result.get('message', '?')))[:200]}")

        except Exception as e:
            results["error"] += 1
            print(f"  FAILED: {e}")

        print()

    # Summary
    print(f"{'='*70}")
    print(f"RESULTS: {results['success']}/{results['total']} completed, {results['error']} errors")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
