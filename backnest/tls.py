"""Generate an ephemeral local test certificate; never commit a private key.

Production deployments pass a real certificate/key to the remote service.  Tests
and the local demo use a throwaway self-signed certificate generated under the
git-ignored `.backnest/tls/` directory.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import NamedTuple


class TlsUnavailable(RuntimeError):
    pass


class TlsPaths(NamedTuple):
    certfile: str
    keyfile: str
    cafile: str


_OPENSSL_CANDIDATES = [
    r"C:\Program Files\Git\usr\bin\openssl.exe",
    r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    "/usr/bin/openssl",
    "/usr/local/bin/openssl",
    "/opt/homebrew/bin/openssl",
]


def _openssl_path() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    for candidate in _OPENSSL_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _paths(directory: Path) -> TlsPaths:
    return TlsPaths(str(directory / "server.pem"), str(directory / "server.key"), str(directory / "ca.pem"))


def _generate_with_cryptography(directory: Path) -> None:
    from datetime import datetime, timedelta, timezone
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BackNest Test"),
        x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (directory / "server.key").write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (directory / "server.pem").write_bytes(pem)
    (directory / "ca.pem").write_bytes(pem)


def _generate_with_openssl(directory: Path) -> None:
    openssl = _openssl_path()
    if not openssl:
        raise TlsUnavailable("未找到 openssl，也没有可用的 cryptography 包；无法生成本地测试证书。")
    config = directory / "openssl.cnf"
    config.write_text(
        "[req]\n"
        "distinguished_name=dn\n"
        "x509_extensions=v3\n"
        "prompt=no\n"
        "[dn]\n"
        "CN=127.0.0.1\n"
        "O=BackNest Test\n"
        "C=CN\n"
        "[v3]\n"
        "subjectAltName=IP:127.0.0.1,DNS:localhost\n"
        "basicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    command = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
               "-days", "3650", "-keyout", str(directory / "server.key"),
               "-out", str(directory / "server.pem"), "-config", str(config)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TlsUnavailable(f"openssl 执行失败：{error}") from error
    if completed.returncode != 0:
        raise TlsUnavailable(f"openssl 生成证书失败：{completed.stderr[-1000:]}")
    (directory / "ca.pem").write_bytes((directory / "server.pem").read_bytes())
    try:
        config.unlink()
    except OSError:
        pass


def ensure_test_cert(directory: Path | str | None = None) -> TlsPaths:
    """Return cert/key/CA paths, generating a throwaway localhost certificate.

    The directory defaults to `$BACKNEST_TLS_DIR` or `.backnest/tls` under the
    repository.  It should be git-ignored.
    """
    root = Path(__file__).resolve().parents[1]
    if directory is None:
        directory = os.environ.get("BACKNEST_TLS_DIR") or (root / ".backnest" / "tls")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = _paths(directory)
    if Path(paths.certfile).is_file() and Path(paths.keyfile).is_file():
        if not Path(paths.cafile).is_file():
            Path(paths.cafile).write_bytes(Path(paths.certfile).read_bytes())
        return paths
    for old in (paths.certfile, paths.keyfile, paths.cafile):
        try:
            Path(old).unlink()
        except OSError:
            pass
    try:
        _generate_with_cryptography(directory)
    except ImportError:
        _generate_with_openssl(directory)
    if not (Path(paths.certfile).is_file() and Path(paths.keyfile).is_file()):
        raise TlsUnavailable("测试证书生成后文件缺失。")
    return paths
