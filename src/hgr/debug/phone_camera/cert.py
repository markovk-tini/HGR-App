"""Self-signed TLS cert chain for the embedded phone-camera server.

iOS Safari refuses to complete a WSS handshake when the server presents
a single self-signed cert that tries to be both root-of-trust and leaf —
even when the user has installed and trusted it as a profile. The fix
is the standard mkcert-style layout: a persistent self-signed **root
CA** (installed and trusted on the phone once, ever) plus a short-lived
**server leaf cert** signed by the root (regenerated per LAN IP). The
server presents the leaf + root as a chain, iOS walks the chain, finds
the trusted root, and accepts the leaf.

File layout under ~/.touchless/certs/:
  touchless_root_ca_cert.pem   - root CA cert (served for iOS install)
  touchless_root_ca_key.pem    - root CA private key (never leaves PC)
  phone_camera_server_cert.pem - leaf cert signed by root, SAN = LAN IP
  phone_camera_server_key.pem  - leaf cert's private key
  phone_camera_server_chain.pem- leaf + root concatenated (for TLS)

Only the root CA cert is downloaded to / installed on the phone. The
leaf cert is regenerated transparently whenever the LAN IP changes —
no user action required.
"""
from __future__ import annotations

import datetime
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


_CERT_DIR = Path.home() / ".touchless" / "certs"
_ROOT_CA_CERT = _CERT_DIR / "touchless_root_ca_cert.pem"
_ROOT_CA_KEY = _CERT_DIR / "touchless_root_ca_key.pem"
_SERVER_CERT = _CERT_DIR / "phone_camera_server_cert.pem"
_SERVER_KEY = _CERT_DIR / "phone_camera_server_key.pem"
_SERVER_CHAIN = _CERT_DIR / "phone_camera_server_chain.pem"
# Legacy single-cert layout from earlier builds. If present, clean up on
# upgrade so users don't end up with stale profiles competing with the
# new root-CA architecture.
_LEGACY_SINGLE_CERT = _CERT_DIR / "phone_camera_cert.pem"
_LEGACY_SINGLE_KEY = _CERT_DIR / "phone_camera_key.pem"

_ROOT_VALIDITY_DAYS = 3650  # 10y — installed on phone once, never reinstalled
_LEAF_VALIDITY_DAYS = 365


@dataclass(frozen=True)
class PhoneCameraCertPaths:
    # Path the server loads into ssl.SSLContext.load_cert_chain. This
    # is the chain file: leaf cert + root CA, in that order.
    cert_path: Path
    # Path to the leaf cert's private key. Server-only.
    key_path: Path
    # Path to the root CA cert for user download / iOS install.
    ca_cert_path: Path
    # Current LAN IP the leaf cert was issued for.
    lan_ip: str


def detect_lan_ip() -> str:
    """Best-effort LAN IP of this machine. Uses the classic UDP-connect
    trick (no packets actually sent) to ask the OS for the route it
    would use for an external destination."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        return "127.0.0.1"


def _clean_legacy_files() -> None:
    for p in (_LEGACY_SINGLE_CERT, _LEGACY_SINGLE_KEY):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _load_or_create_root_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Load the persistent root CA, creating it on first run.

    The root key must never change — any change invalidates every leaf
    cert ever issued under it AND invalidates the trust the user has
    extended to the previous root on their phone. Regenerating the
    root would force the user to re-install and re-trust on every
    affected device, which defeats the whole point of the split
    architecture.
    """
    if _ROOT_CA_CERT.exists() and _ROOT_CA_KEY.exists():
        try:
            cert = x509.load_pem_x509_certificate(_ROOT_CA_CERT.read_bytes())
            key = serialization.load_pem_private_key(_ROOT_CA_KEY.read_bytes(), password=None)
            return cert, key  # type: ignore[return-value]
        except Exception:
            # Corrupt cached root; regenerate.
            pass

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Touchless Root CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Touchless"),
    ])
    public_key = key.public_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_ROOT_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    _write_bytes(_ROOT_CA_CERT, cert.public_bytes(serialization.Encoding.PEM))
    _write_bytes(
        _ROOT_CA_KEY,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    return cert, key


def _leaf_valid_for_ip(cert_bytes: bytes, lan_ip: str, root_cert: x509.Certificate) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except Exception:
        return False
    # Expiry
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        if now >= not_after:
            return False
    except Exception:
        return False
    # Issued by our current root (if the root got regenerated the leaf
    # must be re-issued under the new root)
    try:
        if cert.issuer != root_cert.subject:
            return False
    except Exception:
        return False
    # SAN covers current LAN IP
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for entry in san:
            if isinstance(entry, x509.IPAddress) and str(entry.value) == lan_ip:
                return True
    except Exception:
        pass
    return False


def _issue_server_leaf(
    lan_ip: str,
    root_cert: x509.Certificate,
    root_key: rsa.RSAPrivateKey,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"Touchless Server ({lan_ip})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Touchless"),
    ])
    sans = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    try:
        sans.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
    except Exception:
        pass
    public_key = key.public_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(root_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_LEAF_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_cert.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )
    return cert, key


def ensure_self_signed_cert(force_regenerate: bool = False) -> PhoneCameraCertPaths:
    """Ensure a valid root-CA + server-leaf chain exists, returning paths.

    - Root CA: generated once and reused across launches. A user who has
      installed + trusted this root on their phone never needs to repeat
      the install, even when their LAN IP changes.
    - Server leaf: reused if already valid for the current LAN IP and
      issued by the current root. Otherwise regenerated fresh.
    """
    _CERT_DIR.mkdir(parents=True, exist_ok=True)
    _clean_legacy_files()

    lan_ip = detect_lan_ip()
    root_cert, root_key = _load_or_create_root_ca()

    need_new_leaf = force_regenerate
    if not need_new_leaf:
        if not (_SERVER_CERT.exists() and _SERVER_KEY.exists() and _SERVER_CHAIN.exists()):
            need_new_leaf = True
        else:
            try:
                if not _leaf_valid_for_ip(_SERVER_CERT.read_bytes(), lan_ip, root_cert):
                    need_new_leaf = True
            except Exception:
                need_new_leaf = True

    if need_new_leaf:
        leaf_cert, leaf_key = _issue_server_leaf(lan_ip, root_cert, root_key)
        _write_bytes(_SERVER_CERT, leaf_cert.public_bytes(serialization.Encoding.PEM))
        _write_bytes(
            _SERVER_KEY,
            leaf_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
        # Chain file is what ssl.SSLContext.load_cert_chain consumes for
        # the server's presented chain: leaf first, then root.
        chain_pem = (
            leaf_cert.public_bytes(serialization.Encoding.PEM)
            + root_cert.public_bytes(serialization.Encoding.PEM)
        )
        _write_bytes(_SERVER_CHAIN, chain_pem)

    return PhoneCameraCertPaths(
        cert_path=_SERVER_CHAIN,
        key_path=_SERVER_KEY,
        ca_cert_path=_ROOT_CA_CERT,
        lan_ip=lan_ip,
    )
