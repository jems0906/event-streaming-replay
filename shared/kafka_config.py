import os
import ssl
from typing import Any, Dict


def build_kafka_common_config() -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "bootstrap_servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    }

    security_protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    sasl_mechanism = os.getenv("KAFKA_SASL_MECHANISM", "")
    sasl_username = os.getenv("KAFKA_SASL_USERNAME", "")
    sasl_password = os.getenv("KAFKA_SASL_PASSWORD", "")

    if security_protocol and security_protocol != "PLAINTEXT":
        config["security_protocol"] = security_protocol

    if sasl_mechanism:
        config["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        config["sasl_plain_username"] = sasl_username
    if sasl_password:
        config["sasl_plain_password"] = sasl_password

    if security_protocol in {"SSL", "SASL_SSL"}:
        config["ssl_context"] = ssl.create_default_context()

    return config