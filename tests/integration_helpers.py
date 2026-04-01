"""
Integration helpers for Docker/Kafka mode checks.
Inspects Kafka topics, consumer lag, and recent service logs.
"""

import json
import subprocess
import sys
from typing import Any, Dict, List, Optional


def run_cmd(cmd: str, check: bool = True) -> str:
    """Run shell command and return output."""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def list_kafka_topics() -> List[str]:
    """List all Kafka/Redpanda topics."""
    try:
        output = run_cmd(
            "docker exec redpanda rpk topic list -o json",
            check=False,
        )
        if output:
            topics = json.loads(output)
            return [t.get("name", "") for t in topics]
        return []
    except Exception as e:
        print(f"Error listing topics: {e}")
        return []


def get_topic_partitions(topic: str) -> Optional[int]:
    """Get partition count for a topic."""
    try:
        output = run_cmd(
            f'docker exec redpanda rpk topic describe {topic} -o json',
            check=False,
        )
        if output:
            info = json.loads(output)
            partitions = info.get("partitions", [])
            return len(partitions)
        return None
    except Exception:
        return None


def get_topic_message_count(topic: str) -> int:
    """Approximate message count in a topic (via consumer group offset)."""
    try:
        output = run_cmd(
            f"docker exec redpanda rpk topic info {topic} -o json",
            check=False,
        )
        if output:
            info = json.loads(output)
            high_watermark = info.get("high_watermark", 0)
            return high_watermark
        return 0
    except Exception:
        return 0


def get_consumer_group_lag(group: str) -> Optional[Dict[str, Any]]:
    """Get consumer group lag information."""
    try:
        output = run_cmd(
            f"docker exec redpanda rpk group describe {group} -o json",
            check=False,
        )
        if output:
            return json.loads(output)
        return None
    except Exception:
        return None


def check_service_logs(service: str, lines: int = 20) -> str:
    """Fetch recent logs from a service."""
    try:
        return run_cmd(
            f"docker compose logs --tail {lines} {service}",
            check=False,
        )
    except Exception as e:
        return f"Error fetching logs: {e}"


def run_integration_checks() -> None:
    """Run all integration checks."""
    print("\n" + "=" * 60)
    print("KAFKA & SYSTEM INTEGRATION CHECKS")
    print("=" * 60 + "\n")

    print("1. Topics:")
    topics = list_kafka_topics()
    for t in topics:
        partitions = get_topic_partitions(t)
        msg_count = get_topic_message_count(t)
        print(f"   {t:30s} partitions={partitions} messages~{msg_count}")

    print("\n2. Consumer Groups:")
    groups = ["core-consumer-group"]
    for group in groups:
        lag_info = get_consumer_group_lag(group)
        if lag_info:
            print(f"   {group}: {json.dumps(lag_info, indent=2)}")
        else:
            print(f"   {group}: (no info)")

    print("\n3. Recent Logs:")
    for service in ["gateway", "core", "replay"]:
        print(f"\n   {service.upper()}:")
        logs = check_service_logs(service, lines=5)
        for line in logs.split("\n")[-5:]:
            if line.strip():
                print(f"     {line}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    run_integration_checks()
