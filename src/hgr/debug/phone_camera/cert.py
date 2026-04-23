"""Self-signed TLS certificate for the embedded phone-camera server.

iOS Safari refuses to call `getUserMedia()` from any non-localhost origin
over plain HTTP, so our embedded server has to serve HTTPS. A single
persistent self-signed cert lives in `~/.touchless/certs/`, regenerated
only when the machine's current LAN IP has left the cert's SAN list.

The user still has to tap through a "not trusted" warning on first
visit per session — that's inherent to self-signed certs — but
regenerating on every launch would invalidate any already-approved
browser session on the phone, so we cache aggressively.
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
_CERT_FILE = _CERT_DIR / "phone_camera_cert.pem"
_KEY_FILE = _CERT_DIR / "phone_camera_key.pem"
_VALIDITY_DAYS = 365


@dataclass(frozen=True)
class PhoneCameraCertPaths:
    cert_path: Path
    key_path: Path
    lan_ip: str


def detect_lan_ip() -> str:
    """Best-effort LAN IP of this machine — the address the phone will
    connect to. Uses the classic UDP-connect-without-sending trick,
    which asks the OS to pick the route it would use for an internet
    destination; no packets are actually sent."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        return "127.0.0.1"


def _san_entries(lan_ip: str):
    sans = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    try:
        sans.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
    except Exception:
        pass
    return sans


def _existing_cert_covers_ip(cert_bytes: bytes, lan_ip: str) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except Exception:
        return False
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        # cryptography >= 42 exposes timezone-aware fields; fall back to
        # naive where needed.
        not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        if now >= not_after:
            return False
    except Exception:
        return False
    # iOS 13+ requires server certs to carry ExtendedKeyUsage=serverAuth
    # along with KeyUsage — a cert missing either of these will install
    # and appear "trusted" in iOS Settings but still fail at TLS handshake
    # time, silently stalling WSS connections. If an older cert from a
    # prior Touchless build lacks these extensions, treat it as stale and
    # regenerate.
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.SERVER_AUTH not in list(eku):
            return False
    except x509.ExtensionNotFound:
        return False
    except Exception:
        return False
    try:
        cert.extensions.get_extension_for_class(x509.KeyUsage)
    except x509.ExtensionNotFound:
        return False
    except Exception:
        return False
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for entry in san_ext:
            if isinstance(entry, x509.IPAddress) and str(entry.value) == lan_ip:
                return True
    except Exception:
        pass
    return False


def ensure_self_signed_cert(force_regenerate: bool = False) -> PhoneCameraCertPaths:
    """Return a cert/key pair valid for the current LAN IP.

    Reuses an existing cert when it already covers this machine's IP and
    hasn't expired. Regenerates only when needed so previously-trusted
    phone browser sessions stay valid across app restarts.
    """
    lan_ip = detect_lan_ip()
    _CERT_DIR.mkdir(parents=True, exist_ok=True)

    if (
        not force_regenerate
        and _CERT_FILE.exists()
        and _KEY_FILE.exists()
    ):
        try:
            cert_bytes = _CERT_FILE.read_bytes()
            if _existing_cert_covers_ip(cert_bytes, lan_ip):
                return PhoneCameraCertPaths(cert_path=_CERT_FILE, key_path=_KEY_FILE, lan_ip=lan_ip)
        except Exception:
            pass

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Touchless Phone Camera"),
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
        .not_valid_after(now + datetime.timedelta(days=_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(_san_entries(lan_ip)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # iOS 13+ requires these three extensions or it will silently
        # reject the cert during TLS handshake, even if the user has
        # "trusted" the installed profile. ExtendedKeyUsage=serverAuth
        # is the single most important one; skipping it is the usual
        # reason "self-signed cert on iOS Safari" tutorials stop working.
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
            x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    _KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    _CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return PhoneCameraCertPaths(cert_path=_CERT_FILE, key_path=_KEY_FILE, lan_ip=lan_ip)
