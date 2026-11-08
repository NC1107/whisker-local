from __future__ import annotations

import stat
from pathlib import Path

import pytest
from whiskerless import pki

from whisker_local.certs import IncompleteCa, _names_host, ensure, main


def test_creates_a_chain_naming_the_host(tmp_path: Path):
    ensure(tmp_path, "192.168.1.50")
    ca = pki.read_pair(tmp_path / "ca.crt", tmp_path / "ca.key")
    server = pki.read_pair(tmp_path / "server.crt", tmp_path / "server.key")
    assert pki.is_signed_by(server.cert_pem, ca.cert_pem)
    assert _names_host(server.cert_pem, "192.168.1.50")


def test_private_keys_are_not_world_readable(tmp_path: Path):
    ensure(tmp_path, "192.168.1.50")
    for name in ("ca.key", "server.key"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_rerun_changes_nothing(tmp_path: Path):
    ensure(tmp_path, "192.168.1.50")
    before = {p.name: p.read_text() for p in tmp_path.iterdir()}
    ensure(tmp_path, "192.168.1.50")
    assert {p.name: p.read_text() for p in tmp_path.iterdir()} == before


def test_new_address_reissues_server_cert_but_never_the_ca(tmp_path: Path):
    ensure(tmp_path, "192.168.1.50")
    ca_before = (tmp_path / "ca.crt").read_text()

    ensure(tmp_path, "10.0.0.200")

    server = (tmp_path / "server.crt").read_text()
    assert (tmp_path / "ca.crt").read_text() == ca_before
    assert _names_host(server, "10.0.0.200")
    assert not _names_host(server, "192.168.1.50")


@pytest.mark.parametrize("lost", ["ca.key", "ca.crt"])
def test_half_a_ca_is_refused_instead_of_replaced(tmp_path: Path, lost: str):
    ensure(tmp_path, "192.168.1.50")
    (tmp_path / lost).unlink()
    before = {p.name: p.read_text() for p in tmp_path.iterdir()}

    with pytest.raises(IncompleteCa, match="Refusing to create a new CA"):
        ensure(tmp_path, "192.168.1.50")
    assert {p.name: p.read_text() for p in tmp_path.iterdir()} == before


def test_command_line_exits_non_zero_for_half_a_ca(tmp_path: Path, capsys, monkeypatch):
    ensure(tmp_path, "192.168.1.50")
    (tmp_path / "ca.key").unlink()
    monkeypatch.setattr("sys.argv", ["certs", "--host", "192.168.1.50", "--out", str(tmp_path)])

    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    assert "Refusing to create a new CA" in capsys.readouterr().err
