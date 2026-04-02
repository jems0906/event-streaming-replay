#!/usr/bin/env python3
"""
Smoke test suite for the event streaming & replay platform.
Tests: live capture → Kafka → core processing → metrics → replay.

Usage:
  python tests/smoke_tests.py
"""

import asyncio
import json
import time
from typing import Any, Dict, List

import httpx


class SmokeTestRunner:
    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:8000",
        core_url: str = "http://127.0.0.1:8001",
        replay_url: str = "http://127.0.0.1:8002",
        prometheus_url: str = "http://127.0.0.1:9090",
    ):
        self.gateway_url = gateway_url
        self.core_url = core_url
        self.replay_url = replay_url
        self.prometheus_url = prometheus_url
        self.test_results: List[Dict[str, Any]] = []

    def log_test(self, name: str, status: str, details: str = "") -> None:
        msg = f"[{status}] {name}"
        if details:
            msg += f" — {details}"
        print(msg)
        self.test_results.append({"test": name, "status": status, "details": details})

    async def test_health_checks(self) -> bool:
        """Verify all services are responding to /health."""
        print("\n=== Health Checks ===")
        async with httpx.AsyncClient(timeout=5.0) as client:
            for name, url in [
                ("Gateway", f"{self.gateway_url}/health"),
                ("Core", f"{self.core_url}/health"),
                ("Replay", f"{self.replay_url}/health"),
            ]:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        self.log_test(f"{name} health", "PASS", str(data))
                    else:
                        self.log_test(f"{name} health", "FAIL", f"status={resp.status_code}")
                        return False
                except Exception as e:
                    self.log_test(f"{name} health", "FAIL", str(e))
                    return False
        return True

    async def test_live_capture(self, count: int = 5) -> bool:
        """Send live traffic and verify capture."""
        print("\n=== Live Traffic Capture ===")
        event_ids = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for i in range(count):
                payload = {
                    "user_id": 100 + i,
                    "action": f"test_event_{i}",
                    "timestamp": int(time.time() * 1000),
                }
                try:
                    resp = await client.post(f"{self.gateway_url}/ingest", json=payload)
                    if resp.status_code == 202:
                        data = resp.json()
                        event_ids.append(data.get("event_id"))
                        traffic_type = data.get("traffic_type")
                        self.log_test(
                            f"Capture event {i+1}",
                            "PASS",
                            f"event_id={data.get('event_id', 'N/A')[:8]}... traffic={traffic_type}",
                        )
                    else:
                        self.log_test(f"Capture event {i+1}", "FAIL", f"status={resp.status_code}")
                        return False
                except Exception as e:
                    self.log_test(f"Capture event {i+1}", "FAIL", str(e))
                    return False

        self.log_test("Live capture", "PASS", f"captured {len(event_ids)} events")
        return True

    async def test_metrics_endpoint(self) -> bool:
        """Verify metrics endpoints are collecting data."""
        print("\n=== Metrics Collection ===")
        async with httpx.AsyncClient(timeout=10.0) as client:
            for name, url in [
                ("Gateway", f"{self.gateway_url}/metrics"),
                ("Core", f"{self.core_url}/metrics"),
                ("Replay", f"{self.replay_url}/metrics"),
            ]:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        content = resp.text
                        lines = len(content.split("\n"))
                        has_data = "gateway_requests_total" in content or "core_processed_events_total" in content or "replay_replayed_events_total" in content
                        status = "PASS" if has_data else "WARN"
                        self.log_test(f"{name} /metrics", status, f"{lines} lines of metrics")
                    else:
                        self.log_test(f"{name} /metrics", "FAIL", f"status={resp.status_code}")
                        return False
                except Exception as e:
                    self.log_test(f"{name} /metrics", "FAIL", str(e))
                    return False
        return True

    async def test_prometheus_scrape(self) -> bool:
        """Check if Prometheus is scraping targets."""
        print("\n=== Prometheus Target Scrape ===")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.prometheus_url}/api/v1/targets")
                if resp.status_code == 200:
                    data = resp.json()
                    active = len(data.get("data", {}).get("activeTargets", []))
                    self.log_test("Prometheus targets", "PASS", f"{active} active scrape targets")
                    return active > 0
                else:
                    self.log_test("Prometheus targets", "FAIL", f"status={resp.status_code}")
                    return False
        except Exception as e:
            self.log_test("Prometheus targets", "FAIL", str(e))
            return False

    async def test_replay(self) -> bool:
        """Trigger a replay and verify dispatch."""
        print("\n=== Traffic Replay ===")
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                await asyncio.sleep(2)

                # Use a 10-minute window so only recent events are replayed
                now_ms = int(time.time() * 1000)
                replay_payload = {
                    "from_timestamp_ms": now_ms - 10 * 60 * 1000,
                    "to_timestamp_ms": now_ms,
                    "environment": "dev",
                    "speedup_factor": 100.0,
                    "max_events": 10,
                    # No target_url — use DEFAULT_REPLAY_TARGET (http://gateway:8000/ingest in Docker)
                }
                resp = await client.post(f"{self.replay_url}/replay", json=replay_payload)

                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status")
                    requested = data.get("requested", 0)
                    success = data.get("success", 0)
                    failed = data.get("failed", 0)
                    replay_id = data.get("replay_id", "N/A")[:8]

                    msg = f"replay_id={replay_id}... requested={requested} success={success} failed={failed}"
                    test_status = "PASS" if status == "ok" else "FAIL"
                    self.log_test("Replay dispatch", test_status, msg)
                    return status == "ok"
                else:
                    self.log_test("Replay dispatch", "FAIL", f"status={resp.status_code}")
                    return False
        except Exception as e:
            self.log_test("Replay dispatch", "FAIL", str(e))
            return False

    async def run_all_tests(self) -> None:
        """Run the full smoke test suite."""
        print("\n" + "=" * 60)
        print("EVENT STREAMING & REPLAY PLATFORM — SMOKE TESTS")
        print("=" * 60)

        all_passed = True

        all_passed &= await self.test_health_checks()
        all_passed &= await self.test_live_capture(count=3)
        await asyncio.sleep(1)
        all_passed &= await self.test_metrics_endpoint()
        all_passed &= await self.test_prometheus_scrape()
        all_passed &= await self.test_replay()

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        passed = sum(1 for r in self.test_results if r["status"] == "PASS")
        total = len(self.test_results)
        print(f"Passed: {passed}/{total}")

        if passed == total:
            print("PASS: All systems operational!")
        else:
            print("WARN: Some tests failed - check local service status and logs.")

        print("\nNext steps:")
        print("  - View Grafana dashboard: http://127.0.0.1:3000 (admin/admin)")
        print("  - View Prometheus metrics: http://127.0.0.1:9090")
        print("  - View distributed traces: http://127.0.0.1:16686")
        print("=" * 60)


async def main() -> None:
    runner = SmokeTestRunner()
    await runner.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
