import os
import csv
import json
import tempfile
from pathlib import Path
from typing import Dict, Any, List
from configuration import logger, logging
from datetime import datetime, timedelta, timezone


def timestamp_within_delta(timestamp_str = "2025-11-17T08:16:29.237040+00:00", delta = 2):
    # Parse ISO 8601 timestamp
    ts = None 
    try:
        ts = datetime.fromisoformat(timestamp_str)
    except Exception as _:
        ts = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        
    # Current time in UTC
    now = datetime.now(timezone.utc)

    # Compute threshold
    threshold = now - timedelta(minutes=delta)

    # Compare
    return ts > threshold


def save_sql_to_csv_atomic(col_names: List[str], rows: List[tuple], output_file: Path) -> None:
    """Atomically write AIS vessel data from DB query results to CSV."""

    if not rows:
        print("No vessels to save.")
        return

    # Convert list of tuples → list of dicts
    dict_rows = [dict(zip(col_names, row)) for row in rows]

    # Ensure directory exists
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file next to target
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=output_file.parent,
        delete=False,
        newline="",
        encoding="utf-8"
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=col_names)
        writer.writeheader()
        writer.writerows(dict_rows)

        # Ensure data is flushed + synced
        tmp.flush()
        os.fsync(tmp.fileno())

        temp_name = tmp.name

    # Atomic rename (POSIX-safe)
    os.replace(temp_name, output_file)

    print(f"✅ Saved {len(rows)} vessels to {output_file}")

