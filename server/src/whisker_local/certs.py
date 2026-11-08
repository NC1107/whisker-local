"""Create the CA the robot is told to trust, and the broker's server certificate.

Uses whiskerless's own generator rather than openssl flags so that the certificates have the
same shape as the ones proven against a real robot's TLS stack.
"""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path

from cryptography import x509
from whiskerless import pki


def _write(path: Path, text: str, mode: int) -> None:
    path.touch(mode=mode, exist_ok=True)
    path.chmod(mode)
    path.write_text(text)


def _names_host(cert_pem: str, host: str) -> bool:
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if host in names.get_values_for_type(x509.DNSName):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address in names.get_values_for_type(x509.IPAddress)


def ensure(out: Path, host: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key = out / "ca.crt", out / "ca.key"
    server_cert, server_key = out / "server.crt", out / "server.key"

    # A robot only ever trusts the CA it was given over Bluetooth, so an existing CA is never
    # replaced. Doing so would strand the robot until someone walks over and re-pairs it.
    if ca_cert.exists() and ca_key.exists():
        ca = pki.read_pair(ca_cert, ca_key)
    else:
        ca = pki.generate_ca("whisker-local CA")
        _write(ca_cert, ca.cert_pem, 0o644)
        _write(ca_key, ca.key_pem, 0o600)

    if server_cert.exists() and server_key.exists():
        existing = pki.read_pair(server_cert, server_key)
        if (
            pki.is_current(existing.cert_pem)
            and pki.is_signed_by(existing.cert_pem, ca.cert_pem)
            and _names_host(existing.cert_pem, host)
        ):
            return

    server = pki.issue_server(ca, host)
    _write(server_cert, server.cert_pem, 0o644)
    _write(server_key, server.key_pem, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="address the robot will connect to")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    ensure(args.out, args.host)


if __name__ == "__main__":
    main()
