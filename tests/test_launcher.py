import platform
import plistlib
import shlex
import stat
import subprocess

import pytest

from vinst import launcher
from vinst.ui.idle import IdleExit


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
