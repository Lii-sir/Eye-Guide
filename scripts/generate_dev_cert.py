from __future__ import annotations

import argparse
import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _parse_hosts(raw_hosts: str) -> list[str]:
    items = [item.strip() for item in raw_hosts.split(",")]
    return [item for item in items if item]


def _build_san_entries(hosts: list[str]) -> list[x509.GeneralName]:
    entries: list[x509.GeneralName] = []
    for host in hosts:
        try:
            ip = ipaddress.ip_address(host)
            entries.append(x509.IPAddress(ip))
        except ValueError:
            entries.append(x509.DNSName(host))
    return entries


def generate_cert(output_dir: Path, hosts: list[str], days: int) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cert_path = output_dir / "dev-cert.pem"
    key_path = output_dir / "dev-key.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EyeGuide Local Dev"),
            x509.NameAttribute(NameOID.COMMON_NAME, hosts[0]),
        ]
    )

    now = datetime.now(timezone.utc)
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(_build_san_entries(hosts)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    cert = cert_builder.sign(private_key=key, algorithm=hashes.SHA256())

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a local HTTPS self-signed cert for EyeGuide.")
    parser.add_argument(
        "--hosts",
        default="localhost,127.0.0.1",
        help="Comma-separated hostnames/IPs to include in SAN.",
    )
    parser.add_argument(
        "--out-dir",
        default=".tmp/certs",
        help="Output directory for dev-cert.pem and dev-key.pem.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=3650,
        help="Certificate validity in days.",
    )
    args = parser.parse_args()

    hosts = _parse_hosts(args.hosts)
    if not hosts:
        raise SystemExit("At least one host is required.")

    cert_path, key_path = generate_cert(Path(args.out_dir), hosts, args.days)
    print(f"Certificate written to: {cert_path}")
    print(f"Private key written to: {key_path}")
    print("Start HTTPS server with:")
    print(
        "uv run uvicorn eyeguide.cloud.server:app --host 0.0.0.0 --port 8443 "
        f"--ssl-certfile \"{cert_path}\" --ssl-keyfile \"{key_path}\""
    )


if __name__ == "__main__":
    main()
