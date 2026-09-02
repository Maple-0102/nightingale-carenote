#!/usr/bin/env python3
"""End-to-end warm-path benchmark through the local HTTP API."""

import json
import os
import statistics
import time
from urllib import request


BASE_URL = os.getenv("CARENOTE_BENCHMARK_URL", "http://127.0.0.1:8000").rstrip("/")
RUNS = int(os.getenv("CARENOTE_BENCHMARK_RUNS", "300"))
WARMUPS = int(os.getenv("CARENOTE_BENCHMARK_WARMUPS", "20"))
TARGET_MS = float(os.getenv("CARENOTE_P95_TARGET_MS", "300"))


def percentile(samples, fraction):
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def json_request(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    with request.urlopen(
        request.Request(BASE_URL + path, data=data, headers=headers, method=method),
        timeout=5,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    session = json_request(
        "/api/demo/session",
        method="POST",
        body={"actor_id": "clinician-lim"},
    )
    token = session["token"]

    for _ in range(WARMUPS):
        json_request("/api/care-note?patient_id=patient-maya", token=token)

    samples = []
    last_payload = None
    for _ in range(RUNS):
        started = time.perf_counter()
        last_payload = json_request("/api/care-note?patient_id=patient-maya", token=token)
        samples.append((time.perf_counter() - started) * 1000)

    if last_payload["actor"]["role"] != "clinician" or not last_payload["entries"]:
        raise RuntimeError("Benchmark response failed its authenticated payload check")

    p95 = percentile(samples, 0.95)
    report = {
        "base_url": BASE_URL,
        "warmups": WARMUPS,
        "runs": RUNS,
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(samples), 3),
        "target_ms": TARGET_MS,
        "target_met": p95 <= TARGET_MS,
        "scope": "HTTP + signed-session verification + RBAC + SQLite + JSON on a warm local container path",
    }
    print(json.dumps(report, indent=2))
    if not report["target_met"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
