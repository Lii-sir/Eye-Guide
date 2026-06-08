"""生成本地开发用的自签名 HTTPS 证书。

用途：
1. 让手机通过 `https://电脑IP:8010/mobile/` 访问移动端页面。
2. 满足手机浏览器对 `getUserMedia()` 安全上下文的要求，从而调用摄像头。

说明：
- 这是开发环境证书，不适用于生产环境。
- 证书会同时写入多个常见主机名和局域网 IP，尽量减少反复生成。
"""

from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CERT_DIR = PROJECT_ROOT / ".tmp" / "dev-cert"
CERT_PATH = CERT_DIR / "localhost-cert.pem"
KEY_PATH = CERT_DIR / "localhost-key.pem"


def collect_host_ips() -> list[str]:
    """收集本机常见可用的局域网地址。

    这里不依赖第三方网络探测库，只取系统可解析到的 IPv4 地址，
    并过滤掉回环地址，方便手机通过局域网访问。
    """

    hostname = socket.gethostname()
    candidates = {"127.0.0.1"}

    try:
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            address = info[4][0]
            if address:
                candidates.add(address)
    except OSError:
        pass

    filtered: list[str] = []
    for item in sorted(candidates):
        try:
            ip_obj = ipaddress.ip_address(item)
        except ValueError:
            continue
        if ip_obj.version == 4:
            filtered.append(str(ip_obj))
    return filtered


def build_certificate() -> tuple[bytes, bytes, list[str]]:
    """构造证书和私钥内容。"""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    hostnames = {
        "localhost",
        "127.0.0.1",
        socket.gethostname(),
        f"{socket.gethostname()}.local",
    }
    ip_entries = collect_host_ips()

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EyeGuide Dev"),
            x509.NameAttribute(NameOID.COMMON_NAME, "EyeGuide Local Dev"),
        ]
    )

    alt_names: list[x509.GeneralName] = []
    for hostname in sorted(hostnames):
        alt_names.append(x509.DNSName(hostname))
    for ip_text in ip_entries:
        alt_names.append(x509.IPAddress(ipaddress.ip_address(ip_text)))

    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_bytes, key_bytes, ip_entries


def main() -> None:
    """写出证书文件并打印可直接使用的路径。"""

    cert_bytes, key_bytes, ip_entries = build_certificate()
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    CERT_PATH.write_bytes(cert_bytes)
    KEY_PATH.write_bytes(key_bytes)

    print("开发证书已生成：")
    print(f"  cert: {CERT_PATH}")
    print(f"  key : {KEY_PATH}")
    print("证书包含的常见访问地址：")
    print("  - localhost")
    for ip_text in ip_entries:
        print(f"  - {ip_text}")


if __name__ == "__main__":
    main()
