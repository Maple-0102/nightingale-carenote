#!/usr/bin/env python3
"""Warm-path service benchmark for the clinician glance payload."""

from pathlib import Path
import json
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from carenote.db import Database
from carenote.service import CareNoteService


def percentile(samples, fraction):
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def main():
    with tempfile.TemporaryDirectory(prefix="nightingale-benchmark-") as temp_dir:
        db = Database(Path(temp_dir) / "benchmark.db")
        db.initialize()
        db.seed_demo()
        service = CareNoteService(db)
        for _ in range(20):
            service.get_care_note("clinician-lim", "patient-maya")
        samples = []
        for _ in range(300):
            started = time.perf_counter()
            service.get_care_note("clinician-lim", "patient-maya")
            samples.append((time.perf_counter() - started) * 1000)
        report = {
            "runs": len(samples),
            "median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(percentile(samples, 0.95), 3),
            "max_ms": round(max(samples), 3),
            "target_ms": 300,
            "target_met": percentile(samples, 0.95) <= 300,
            "scope": "service + SQLite serialization on a warm local path; excludes network and browser render",
        }
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
