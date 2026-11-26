import platform
import plistlib
import shlex
import stat
import subprocess

import pytest

from vinst import agent, launcher
from vinst.ui.idle import IdleExit


@pytest.fixture(autouse=True)
def no_real_agent(monkeypatch, tmp_path):
    """Whether the user's own launch agent is installed mustn't change what these tests see."""
    monkeypatch.setattr(agent, "PLIST", tmp_path / "LaunchAgents" / f"{agent.LABEL}.plist")


def test_idle_exit_waits_for_the_last_page_to_close():
    w = IdleExit(60)
    start = w.idle_since
    assert not w.should_exit(start + 30, open_pages=False)
    assert w.should_exit(start + 61, open_pages=False)  # nobody ever connected
    assert not w.should_exit(start + 1000, open_pages=True)
    assert not w.should_exit(start + 1059, open_pages=False)  # idle counts from the last open page
    assert w.should_exit(start + 1060, open_pages=False)


def test_launcher_bundle_script_runs_and_alerts_on_failure(tmp_path):
    bundle = tmp_path / "Builder.app"
    repo = tmp_path / "my repo"
    repo.mkdir()
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\necho 'Port 4780 is used by another program' >&2\nexit 1\n")
    fake_uv.chmod(0o755)
    launcher.write_launcher(bundle, repo, fake_uv)
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "launcher" and info["LSUIElement"] is True
    script = bundle / "Contents" / "MacOS" / "launcher"
    assert script.stat().st_mode & stat.S_IXUSR
    # Replace the alert with an echo so the test can see what the user would be told.
    text = script.read_text().replace("/usr/bin/osascript", "echo ALERT").replace(" >/dev/null 2>&1", "")
    script.write_text(text)
    out = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True)
    assert out.returncode == 1 and "Port 4780 is used by another program" in out.stdout
    repo.rmdir()
    out = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True)
    assert "folder is missing" in out.stdout


def rosetta() -> bool:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    return subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"], capture_output=True).returncode == 0


@pytest.mark.skipif(not rosetta(), reason="needs Apple silicon with Rosetta")
def test_launcher_started_under_rosetta_runs_the_builder_natively(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\necho translated=$(/usr/sbin/sysctl -n sysctl.proc_translated)\n")
    fake_uv.chmod(0o755)
    bundle = tmp_path / "Builder.app"
    launcher.write_launcher(bundle, repo, fake_uv)
    log = tmp_path / "vinst.log"
    script = bundle / "Contents" / "MacOS" / "launcher"
    script.write_text(script.read_text().replace(shlex.quote(str(launcher.LOG_FILE)), shlex.quote(str(log))))
    out = subprocess.run(["/usr/bin/arch", "-x86_64", "/bin/sh", str(script)], capture_output=True, text=True)
    assert out.returncode == 0 and log.read_text() == "translated=0\n"


def test_open_reuses_a_running_server_and_prefers_the_chrome_app(monkeypatch, tmp_path):
    opened, started, ran = [], [], []
    monkeypatch.setattr(launcher, "builder_running", lambda port: True)
    monkeypatch.setattr(launcher, "start_server", lambda *a: started.append(a))
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    monkeypatch.setattr(launcher, "CHROME_APPS", tmp_path)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    launcher.open_builder(4780, 120)
    assert opened == ["http://127.0.0.1:4780/"] and not started
    app = tmp_path / f"{launcher.APP_NAME}.app"
    app.mkdir()
    monkeypatch.setattr(launcher, "chrome_app", lambda: app)
    launcher.open_builder(4780, 120)
    assert ran == [["open", "-a", str(app)]]


def test_port_taken_by_another_program_is_reported(monkeypatch):
    monkeypatch.setattr(launcher, "builder_running", lambda port: False)
    monkeypatch.setattr(launcher, "port_in_use", lambda port: True)
    with pytest.raises(launcher.LaunchError, match="used by another program"):
        launcher.open_builder(4780, 120)


def test_with_the_agent_the_launcher_waits_for_macos_to_start_the_builder(monkeypatch):
    agent.PLIST.parent.mkdir()
    agent.PLIST.write_bytes(b"")
    calls = iter([False, False, True])
    started = []
    monkeypatch.setattr(launcher, "builder_running", lambda port: next(calls))
    monkeypatch.setattr(launcher, "port_in_use", lambda port: True)  # launchd holds it
    monkeypatch.setattr(launcher, "start_server", lambda *a: started.append(a))
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher, "chrome_app", lambda: None)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)
    launcher.open_builder(4780, 120)
    assert not started


def test_agent_hands_the_port_to_uv_in_this_checkout(tmp_path):
    info = plistlib.loads(agent.plist(tmp_path / ".venv/bin/python", tmp_path / "uv", tmp_path, 4780,
                                      tmp_path / "vinst.log"))
    assert info["ProgramArguments"] == [str(tmp_path / ".venv/bin/python"), "-m", "vinst.agent", str(tmp_path / "uv"),
                                        "4780"]
    assert info["WorkingDirectory"] == str(tmp_path)
    assert info["Sockets"][agent.SOCKET] == {"SockNodeName": "127.0.0.1", "SockServiceName": "4780",
                                             "SockFamily": "IPv4"}
    assert "KeepAlive" not in info and "RunAtLoad" not in info  # only when a page asks for it


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
def test_spawned_process_inherits_the_socket(tmp_path):
    import os
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        os.set_inheritable(s.fileno(), True)
        out = tmp_path / "out"
        pid = agent.spawn_disclaimed(["/bin/sh", "-c", f"[ -e /dev/fd/{s.fileno()} ] && echo yes > {out}"])
        _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0 and out.read_text() == "yes\n"


def test_agent_is_not_installed_over_a_taken_port(monkeypatch):
    ran = []
    monkeypatch.setattr(agent.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    monkeypatch.setattr(launcher, "port_in_use", lambda port: True)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    clock = iter(range(100))
    monkeypatch.setattr(agent.time, "monotonic", lambda: next(clock))
    with pytest.raises(OSError, match="Port 4780 is in use"):
        agent.install(agent.PLIST, agent.PLIST, agent.PLIST, 4780, agent.PLIST)
    assert not any("bootstrap" in cmd for cmd in ran) and not agent.installed()


def test_install_app_keeps_the_launcher_only_until_there_is_a_chrome_app(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from vinst.cli import app

    installed = []
    monkeypatch.setattr(agent, "install", lambda *a: installed.append(a))
    monkeypatch.setattr(launcher, "CHROME_APPS", tmp_path / "Chrome Apps")
    apps = tmp_path / "Applications"
    result = CliRunner().invoke(app, ["install-app", "--folder", str(apps)])
    assert result.exit_code == 0 and installed and (apps / f"{launcher.APP_NAME}.app").exists()
    (tmp_path / "Chrome Apps" / f"{launcher.APP_NAME}.app").mkdir(parents=True)
    result = CliRunner().invoke(app, ["install-app", "--folder", str(apps)])
    assert result.exit_code == 0 and not (apps / f"{launcher.APP_NAME}.app").exists()
    assert "Open the builder with" in result.output


def test_launcher_has_the_icon_and_replaces_one_under_the_old_name(tmp_path):
    old = tmp_path / f"{launcher.OLD_NAMES[0]}.app"
    launcher.write_launcher(old, tmp_path, tmp_path / "uv")
    foreign = tmp_path / "Foreign"
    (foreign / f"{launcher.OLD_NAMES[0]}.app" / "Contents").mkdir(parents=True)
    (foreign / f"{launcher.OLD_NAMES[0]}.app" / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.example.other"}))
    assert launcher.remove_launchers(tmp_path, launcher.OLD_NAMES) == [old] and not old.exists()
    assert launcher.remove_launchers(foreign, launcher.OLD_NAMES) == []
    assert (foreign / f"{launcher.OLD_NAMES[0]}.app").exists()
    bundle = tmp_path / f"{launcher.APP_NAME}.app"
    launcher.write_launcher(bundle, tmp_path, tmp_path / "uv")
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Virtual Instrument Creator" and info["CFBundleIconFile"] == "icon"
    assert (bundle / "Contents" / "Resources" / "icon.icns").read_bytes()[:4] == b"icns"


def test_chrome_app_lookup(tmp_path):
    assert launcher.chrome_app(folder=tmp_path) is None
    (tmp_path / f"{launcher.APP_NAME}.app").mkdir()
    assert launcher.chrome_app(folder=tmp_path).name == f"{launcher.APP_NAME}.app"


def test_log_is_trimmed(tmp_path):
    log = tmp_path / "vinst.log"
    log.write_bytes(b"".join(f"line {i}\n".encode() for i in range(50_000)))
    launcher.trim_log(log, max_bytes=10_000)
    data = log.read_bytes()
    assert len(data) <= 5_000 and data.startswith(b"line ") and data.endswith(b"line 49999\n")


def test_project_root_is_this_checkout():
    root = launcher.project_root()
    assert (root / "src" / "vinst" / "launcher.py").is_file()
