from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
FAKE_PYTHON = "#!/bin/sh\nexit 0\n"


def run(
    *command: str, cwd: Path | None = None, env: dict[str, str] | None = None, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command, cwd=cwd, env=env, input=stdin, capture_output=True, text=True, check=False
    )


def call_install_function(snippet: str) -> str:
    result = run("bash", "-c", f'source "{REPO}/install.sh"; {snippet}')
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("0.0.0.0", "not an address on your network"),  # noqa: S104
        ("256.1.1.1", "not a valid octet"),
        ("10.0.0", "not an IPv4 address"),
        ("hello", "not an IPv4 address"),
        ("127.0.0.1", "this machine only"),
        ("169.254.3.4", "link-local"),
        ("100.64.0.9", "Tailscale"),
        ("100.127.255.254", "Tailscale"),
        ("172.17.0.1", "docker"),
        ("8.8.8.8", "not a private LAN address"),
        ("10.9.9.9", "doesn't have that address"),
    ],
)
def test_unusable_lan_addresses_are_refused(address: str, reason: str):
    assert reason in call_install_function(f"lan_ip_problem {address}")


def test_a_private_address_on_this_machine_is_accepted():
    snippet = "local_addresses() { echo 192.168.1.5; }; lan_ip_problem 192.168.1.5"
    assert call_install_function(snippet) == ""


def test_loopback_needs_the_explicit_flag():
    assert call_install_function("allow_loopback=true; lan_ip_problem 127.0.0.2") == ""


@pytest.mark.parametrize(("zone", "problem"), [("America/New_York", ""), ("UTC", "")])
def test_real_timezones_are_accepted(zone: str, problem: str):
    assert call_install_function(f"timezone_problem {zone}") == problem


@pytest.mark.parametrize("zone", ["Mars/Base", "$(touch x)", "a b"])
def test_made_up_timezones_are_refused(zone: str):
    assert call_install_function(f"timezone_problem '{zone}'") != ""


@pytest.fixture
def fake_docker(tmp_path: Path):
    """A checkout copy with a docker that records its calls and can rewrite the certificate."""
    checkout = tmp_path / "checkout"
    (checkout / "broker").mkdir(parents=True)
    shutil.copy(REPO / "install.sh", checkout / "install.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        f"""#!/bin/bash
echo "$*" >> "{log}"
case "$*" in
    "compose version") ;;
    "compose ps"*) grep -q "compose up -d" "{log}" 2>/dev/null && echo broker-id ;;
    "compose pull"*) [[ -z "${{PULL_FAILS:-}}" ]] || exit 1 ;;
    "compose up -d --wait"*) [[ -z "${{SERVER_UNHEALTHY:-}}" ]] || exit 1 ;;
    "compose run"*)
        mkdir -p data/certs
        printf '%s' "${{CERT_CONTENT:-first}}" > data/certs/server.crt ;;
esac
"""
    )
    docker.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}

    def install(*args: str, extra_env: dict[str, str] | None = None):
        result = run(
            "bash", "install.sh", "--ip", "127.0.0.2", "--allow-loopback",
            "--serial", "LR4C000001", *args,
            cwd=checkout, env={**env, **(extra_env or {})},
        )  # fmt: skip
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls, checkout

    return install


def test_install_records_the_timezone_for_the_server(fake_docker):
    result, _, checkout = fake_docker("--timezone", "Europe/Paris")
    assert result.returncode == 0, result.stderr
    assert "TZ=Europe/Paris" in (checkout / ".env").read_text()


def test_install_detects_a_timezone_when_none_is_given(fake_docker):
    result, _, checkout = fake_docker()
    assert result.returncode == 0, result.stderr
    assert any(
        line.startswith("TZ=") and len(line) > 3
        for line in (checkout / ".env").read_text().splitlines()
    )


def env_value(checkout: Path, name: str) -> str:
    for line in (checkout / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.partition("=")[2]
    raise AssertionError(f"{name} is not in .env")


@pytest.mark.parametrize("kept", ["", "generated-by-install"])
def test_install_replaces_an_empty_or_sample_access_key(fake_docker, kept: str):
    _, _, checkout = fake_docker()
    (checkout / ".env").write_text(f"ACCESS_KEY={kept}\n")

    result, _, _ = fake_docker()

    assert result.returncode == 0, result.stderr
    key = env_value(checkout, "ACCESS_KEY")
    assert key not in ("", "generated-by-install")
    assert len(key) >= 32


def test_install_keeps_a_real_access_key_and_locks_down_an_existing_env(fake_docker):
    _, _, checkout = fake_docker()
    (checkout / ".env").write_text("ACCESS_KEY=chosen-by-owner\n")
    (checkout / ".env").chmod(0o644)

    fake_docker()

    assert env_value(checkout, "ACCESS_KEY") == "chosen-by-owner"
    assert (checkout / ".env").stat().st_mode & 0o777 == 0o600


def test_install_keeps_the_sample_data_settings_on_a_rerun(fake_docker):
    _, _, checkout = fake_docker()
    with (checkout / ".env").open("a") as env:
        env.write("DEMO_MODE=1\nDEMO_WEIGHTS=10.1,9.0\nDEMO_VISIT_EVERY_SECONDS=300\n")

    result, _, _ = fake_docker()

    assert result.returncode == 0, result.stderr
    assert env_value(checkout, "DEMO_MODE") == "1"
    assert env_value(checkout, "DEMO_WEIGHTS") == "10.1,9.0"
    assert env_value(checkout, "DEMO_VISIT_EVERY_SECONDS") == "300"


def test_install_keeps_the_push_relay_on_a_rerun(fake_docker):
    _, _, checkout = fake_docker()
    with (checkout / ".env").open("a") as env:
        env.write("PUSH_RELAY_URL=https://relay.example.com\n")

    result, _, _ = fake_docker()

    assert result.returncode == 0, result.stderr
    assert env_value(checkout, "PUSH_RELAY_URL") == "https://relay.example.com"


def test_install_adds_no_push_relay_to_a_normal_install(fake_docker):
    _, _, checkout = fake_docker()
    assert "PUSH_" not in (checkout / ".env").read_text()


def test_install_leaves_the_sample_data_settings_out_of_a_normal_install(fake_docker):
    _, _, checkout = fake_docker()
    assert "DEMO_" not in (checkout / ".env").read_text()


def test_install_only_reports_running_once_the_server_is_healthy(fake_docker):
    result, calls, _ = fake_docker()
    assert result.returncode == 0, result.stderr
    assert any(call.startswith("compose up -d --wait") for call in calls)
    assert "Running." in result.stdout


def test_install_says_so_when_the_server_never_becomes_healthy(fake_docker):
    result, _, _ = fake_docker(extra_env={"SERVER_UNHEALTHY": "1"})
    assert result.returncode != 0
    assert "did not become healthy" in result.stderr
    assert "Running." not in result.stdout


def test_the_sample_env_is_not_a_usable_credential():
    sample = (REPO / ".env.example").read_text().splitlines()
    assert "ACCESS_KEY=" in sample


def test_install_rejects_a_bad_address_before_touching_docker(fake_docker):
    result, calls, _ = fake_docker("--ip", "0.0.0.0")  # noqa: S104
    assert result.returncode != 0
    assert "can't use 0.0.0.0" in result.stderr
    assert not [call for call in calls if call.startswith("compose build")]


def test_install_prefers_the_published_image(fake_docker):
    result, calls, _ = fake_docker()
    assert result.returncode == 0, result.stderr
    assert "compose pull --quiet server" in calls
    assert "compose build server" not in calls


def test_install_builds_when_the_published_image_cannot_be_pulled(fake_docker):
    result, calls, _ = fake_docker(extra_env={"PULL_FAILS": "1"})
    assert result.returncode == 0, result.stderr
    assert "compose build server" in calls


def test_install_build_flag_skips_the_pull(fake_docker):
    result, calls, _ = fake_docker("--build")
    assert result.returncode == 0, result.stderr
    assert "compose build server" in calls
    assert not [call for call in calls if call.startswith("compose pull")]


def test_first_install_does_not_recreate_the_broker(fake_docker):
    _, calls, _ = fake_docker()
    assert "compose up -d --force-recreate broker" not in calls


def test_rerun_with_unchanged_certificate_leaves_the_broker_alone(fake_docker):
    fake_docker()
    _, calls, _ = fake_docker()
    assert "compose up -d --force-recreate broker" not in calls


def test_a_reissued_certificate_recreates_the_broker(fake_docker):
    fake_docker()
    result, calls, _ = fake_docker(extra_env={"CERT_CONTENT": "renewed"})
    assert result.returncode == 0, result.stderr
    assert "compose up -d --force-recreate broker" in calls


def test_the_broker_acl_names_only_the_installed_robot(fake_docker):
    result, _, checkout = fake_docker()
    assert result.returncode == 0, result.stderr
    acl = checkout / "data" / "acl"
    rules = [line for line in acl.read_text().splitlines() if not line.startswith("#")]
    assert rules == [
        "pattern read prod/LR4/LR4C000001/command",
        "pattern write prod/LR4/LR4C000001/state",
        "pattern write prod/LR4/LR4C000001/activity",
    ]
    assert acl.stat().st_mode & 0o077 == 0


def test_a_changed_serial_recreates_the_broker(fake_docker):
    fake_docker()
    result, calls, _ = fake_docker("--serial", "LR4C000002")
    assert result.returncode == 0, result.stderr
    assert "compose up -d --force-recreate broker" in calls


@pytest.fixture
def connect_checkout(tmp_path: Path):
    """The script in a checkout with stand-ins for python3 and the whiskerless virtualenv."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "server").mkdir()
    shutil.copy(REPO / "scripts" / "connect-robot.sh", checkout / "scripts")
    (checkout / "server" / "pyproject.toml").write_text('dependencies = ["whiskerless==1.2.3"]\n')
    (checkout / "ca.crt").write_text("")
    stub = tmp_path / "stub"
    venv_bin = stub / "state" / "whisker-local" / "robot-tools-1.2.3" / "bin"
    venv_bin.mkdir(parents=True)
    calls = stub / "whiskerless.log"
    for name, body in {
        "python3": FAKE_PYTHON,
        "python": FAKE_PYTHON,
        "pip": FAKE_PYTHON,
        "whiskerless": (
            f'#!/bin/sh\necho "$*" >> "{calls}"\n'
            'case "$*" in "provision --serial"*) exit "${PROVISION_EXIT:-0}" ;; esac\n'
        ),
    }.items():
        path = (venv_bin if name != "python3" else stub) / name
        path.write_text(body)
        path.chmod(0o755)
    return checkout, stub, calls


def connect_args(checkout: Path) -> list[str]:
    return ["--host", "10.0.0.5", "--ca", str(checkout / "ca.crt"), "--serial", "LR4C000001"]


def test_connect_refuses_dry_run_before_anything_happens(connect_checkout):
    checkout, stub, calls = connect_checkout
    env = {
        "PATH": f"{stub}:/usr/bin:/bin",
        "HOME": str(stub),
        "XDG_STATE_HOME": str(stub / "state"),
    }
    script = str(checkout / "scripts" / "connect-robot.sh")
    result = run("bash", script, *connect_args(checkout), "--dry-run", env=env)
    assert result.returncode != 0
    assert "pairing mode" in result.stderr
    assert not calls.exists()


def test_connect_tells_the_owner_how_to_recover_when_provisioning_fails(connect_checkout):
    checkout, stub, _ = connect_checkout
    env = {
        "PATH": f"{stub}:/usr/bin:/bin",
        "HOME": str(stub),
        "XDG_STATE_HOME": str(stub / "state"),
        "PROVISION_EXIT": "1",
    }
    script = str(checkout / "scripts" / "connect-robot.sh")
    result = run("bash", script, *connect_args(checkout), env=env, stdin="\n\n")
    assert result.returncode == 1
    assert "pairing mode with its Wi-Fi cleared" in result.stderr
    assert "Whisker app" in result.stderr


def test_connect_does_not_stop_on_an_empty_argument_list_in_old_bash(connect_checkout):
    checkout, stub, calls = connect_checkout
    if not shutil.which("docker") or run("docker", "image", "inspect", "bash:3.2").returncode:
        pytest.skip("needs the bash:3.2 image, the shell macOS ships")
    script = checkout / "scripts" / "connect-robot.sh"
    result = run(
        "docker", "run", "--rm", "-i", "-v", f"{checkout}:{checkout}:z", "-v", f"{stub}:{stub}:z",
        "-e", f"XDG_STATE_HOME={stub / 'state'}", "-e", f"HOME={stub}",
        "-e", f"PATH={stub}:/usr/local/bin:/usr/bin:/bin",
        "bash:3.2", "bash", str(script), *connect_args(checkout),
        stdin="\n\n",
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "provision --serial LR4C000001" in calls.read_text()


class StatusHandler(BaseHTTPRequestHandler):
    online = False

    def do_GET(self):
        authorised = self.headers.get("Authorization") == "Bearer secret"
        # Cloudflare answers Python's default user agent with a 403.
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            authorised = False
        self.send_response(200 if authorised else 401)
        self.end_headers()
        self.wfile.write(json.dumps({"online": type(self).online}).encode())

    def log_message(self, format, *args):
        pass


@pytest.fixture
def status_server():
    server = HTTPServer(("127.0.0.1", 0), StatusHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/api/status"
    server.shutdown()


def wait_for_robot(url: str, key: str, seconds: int):
    script = f'source "{REPO}/scripts/connect-robot.sh"; wait_for_robot "{url}" "{key}" {seconds}'
    return run("bash", "-c", script, env={**os.environ, "POLL_SECONDS": "1"})


def test_waiting_succeeds_once_the_server_sees_the_robot(status_server):
    StatusHandler.online = True
    assert wait_for_robot(status_server, "secret", 5).returncode == 0


def test_waiting_gives_up_when_the_robot_never_appears(status_server):
    StatusHandler.online = False
    assert wait_for_robot(status_server, "secret", 1).returncode == 1


def test_waiting_does_not_count_a_rejected_key_as_online(status_server):
    StatusHandler.online = True
    assert wait_for_robot(status_server, "wrong", 1).returncode == 1


def test_ci_stack_smoke_install_passes_its_own_address_check():
    workflow = (REPO / ".github/workflows/ci.yml").read_text()
    command = next(line for line in workflow.splitlines() if "./install.sh" in line)
    flags = command.split("./install.sh", 1)[1].split()
    address = flags[flags.index("--ip") + 1]
    loopback = "allow_loopback=true; " if "--allow-loopback" in flags else ""
    assert call_install_function(f"{loopback}lan_ip_problem {address}") == ""


@pytest.fixture
def demo_checkout(tmp_path: Path):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "end-demo.sh", checkout / "scripts" / "end-demo.sh")
    (checkout / "data" / "db" / "cat-photos").mkdir(parents=True)
    (checkout / "data" / "db" / "whisker-local.db").write_text("sample")
    (checkout / "data" / "db" / "cat-photos" / "1.jpg").write_text("photo")
    (checkout / "data" / "certs").mkdir()
    (checkout / ".env").write_text("LAN_IP=127.0.0.2\nDEMO_MODE=1\nACCESS_KEY=k\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(f'#!/bin/bash\necho "$*" >> "{log}"\n')
    docker.chmod(0o755)

    def end() -> tuple[subprocess.CompletedProcess[str], list[str]]:
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
        result = run("bash", "scripts/end-demo.sh", cwd=checkout, env=env)
        return result, log.read_text().splitlines() if log.exists() else []

    return checkout, end


def test_ending_the_demo_sets_the_sample_database_aside(demo_checkout):
    checkout, end = demo_checkout

    result, _ = end()

    assert result.returncode == 0, result.stderr
    [aside] = [p for p in (checkout / "data").iterdir() if p.name.startswith("db-demo-")]
    assert (aside / "whisker-local.db").read_text() == "sample"
    assert (aside / "cat-photos" / "1.jpg").read_text() == "photo"
    assert list((checkout / "data" / "db").iterdir()) == []
    assert aside.name in result.stdout


def test_ending_the_demo_stops_the_simulator_before_the_server_and_restarts_cleanly(demo_checkout):
    _, end = demo_checkout

    _, calls = end()

    assert calls == [
        "compose --profile demo rm --stop --force demo-robot",
        "compose stop server",
        "compose up -d",
    ]


def test_ending_the_demo_clears_the_demo_setting_and_keeps_the_rest(demo_checkout):
    checkout, end = demo_checkout
    (checkout / ".env").chmod(0o600)

    end()

    assert (checkout / ".env").read_text() == "LAN_IP=127.0.0.2\nACCESS_KEY=k\n"
    assert (checkout / ".env").stat().st_mode & 0o777 == 0o600
    assert not (checkout / ".env.new").exists()


def test_ending_the_demo_also_clears_the_simulator_settings(demo_checkout):
    checkout, end = demo_checkout
    (checkout / ".env").write_text(
        "LAN_IP=127.0.0.2\nDEMO_MODE=1\nDEMO_WEIGHTS=10.1,9.0\n"
        "DEMO_VISIT_EVERY_SECONDS=60\nACCESS_KEY=k\n"
    )

    end()

    assert (checkout / ".env").read_text() == "LAN_IP=127.0.0.2\nACCESS_KEY=k\n"


def test_ending_a_demo_that_is_not_running_touches_nothing(demo_checkout):
    checkout, end = demo_checkout
    (checkout / ".env").write_text("LAN_IP=127.0.0.2\n")

    result, calls = end()

    assert result.returncode == 1
    assert "not running the demo" in result.stderr
    assert calls == []
    assert (checkout / "data" / "db" / "whisker-local.db").read_text() == "sample"


def compose_service(name: str) -> str:
    text = (REPO / "compose.yaml").read_text()
    start = text.index(f"\n  {name}:\n")
    following = [
        text.find(f"\n  {other}:\n", start + 1) for other in ("broker", "server", "demo-robot")
    ]
    ends = [index for index in following if index != -1]
    return text[start : min(ends)] if ends else text[start:]


def test_the_simulator_only_starts_with_the_demo_profile():
    demo = compose_service("demo-robot")
    assert 'profiles: ["demo"]' in demo
    assert "whisker_local.simulator" in demo
    assert "profiles" not in compose_service("server")
    assert "profiles" not in compose_service("broker")


def test_the_simulator_uses_the_internal_broker_port_without_certificates():
    demo = compose_service("demo-robot")
    assert "- broker\n" in demo
    assert "--ca" not in demo
    assert "ports:" not in demo
    assert "data/certs" not in demo


def test_the_robot_listener_limits_connections_and_packet_size():
    config = (REPO / "broker" / "mosquitto.conf").read_text()
    robot_listener = config[config.index("listener 8883") :]
    assert "max_connections 10" in robot_listener
    assert "max_packet_size 16384" in robot_listener
    assert "max_queued_messages" in config


def test_the_ios_app_explains_why_the_photo_picker_needs_access():
    # App Store processing rejects a build whose image_picker code has no purpose strings.
    info = plistlib.loads((REPO / "app/ios/Runner/Info.plist").read_bytes())
    assert info["NSPhotoLibraryUsageDescription"]
    assert info["NSCameraUsageDescription"]


def test_the_app_store_icon_has_no_alpha_channel():
    # App Store Connect refuses an upload whose marketing icon has transparency.
    icon = "app/ios/Runner/Assets.xcassets/AppIcon.appiconset/Icon-App-1024x1024@1x.png"
    with Image.open(REPO / icon) as image:
        assert image.size == (1024, 1024)
        assert image.mode == "RGB"
