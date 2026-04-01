#!/usr/bin/env python3
"""
Load test helper: generates sustained traffic for performance testing.

Usage:
    python tests/load_test.py --duration 30 --rps 10 --target http://127.0.0.1:8000/ingest
"""

import argparse
import asyncio
import time
from typing import Any, Dict

import httpx


async def load_test(
    target_url: str,
    duration_seconds: int,
    rps: float,
) -> Dict[str, Any]:
    """
    Send traffic at target_url with sustained RPS for duration.
    Returns stats: total_sent, total_errors, avg_latency, p95_latency.
    """
    interval_s = 1.0 / rps
    start_time = time.time()
    latencies = []
    errors = 0
    sent = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.time() - start_time < duration_seconds:
            payload = {
                "user_id": sent % 1000,
                "iteration": sent,
                "timestamp_ms": int(time.time() * 1000),
            }

            request_start = time.perf_counter()
            try:
                resp = await client.post(target_url, json=payload)
                latency_ms = (time.perf_counter() - request_start) * 1000
                latencies.append(latency_ms)

                if 200 <= resp.status_code < 300:
                    sent += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

            elapsed = time.time() - start_time
            if elapsed < duration_seconds:
                await asyncio.sleep(max(0, interval_s - (time.perf_counter() - request_start)))

    latencies.sort()
    avg_lat = sum(latencies) / len(latencies) if latencies else 0
    p95_lat = latencies[int(len(latencies) * 0.95)] if latencies else 0
    actual_rps = sent / (time.time() - start_time)

    return {
        "total_sent": sent,
        "total_errors": errors,
        "duration_seconds": duration_seconds,
        "target_rps": rps,
        "actual_rps": actual_rps,
        "avg_latency_ms": avg_lat,
        "p95_latency_ms": p95_lat,
        "error_rate": errors / (sent + errors) if (sent + errors) > 0 else 0,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Load test the event platform")
    parser.add_argument(
        "--duration",
        type=int,
        default=30,
        help="Test duration in seconds (default 30)",
    )
    parser.add_argument(
        "--rps",
        type=float,
        default=10.0,
        help="Target requests per second (default 10)",
    )
    parser.add_argument(
        "--target",
        type=str,
        default="http://127.0.0.1:8000/ingest",
        help="Target URL (default http://127.0.0.1:8000/ingest)",
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("LOAD TEST")
    print("=" * 60)
    print(f"Target: {args.target}")
    print(f"Duration: {args.duration}s")
    print(f"Target RPS: {args.rps}")
    print("Starting in 2 seconds...\n")

    await asyncio.sleep(2)
    start = time.time()

    stats = await load_test(args.target, args.duration, args.rps)

    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Duration: {elapsed:.2f}s")
    print(f"Sent: {stats['total_sent']}")
    print(f"Errors: {stats['total_errors']}")
    print(f"Actual RPS: {stats['actual_rps']:.2f}")
    print(f"Error Rate: {stats['error_rate']*100:.1f}%")
    print(f"Avg Latency: {stats['avg_latency_ms']:.2f}ms")
    print(f"p95 Latency: {stats['p95_latency_ms']:.2f}ms")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
