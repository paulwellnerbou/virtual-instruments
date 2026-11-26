"""The launch agent: macOS holds the builder's port and starts the builder when a page connects.

So the builder installed as a Chrome app opens by itself, and the server still runs only while a page
is open.
"""

import ctypes
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

LABEL = "io.github.vinst.instrument-builder"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
SOCKET = "Listeners"
IDLE_EXIT_S = 120
LIBSYSTEM = "/usr/lib/libSystem.B.dylib"


def plist(python: Path, uv: Path, repo: Path, port: int, log: Path) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "vinst.agent", str(uv), str(port)],
        "WorkingDirectory": str(repo),
        "Sockets": {SOCKET: {"SockNodeName": "127.0.0.1", "SockServiceName": str(port), "SockFamily": "IPv4"}},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "ProcessType": "Interactive",  # recording mustn't be throttled like a background job
    })


def installed() -> bool:
    return PLIST.is_file()


def domain() -> str:
    return f"gui/{os.getuid()}"


def install(python: Path, uv: Path, repo: Path, port: int, log: Path) -> None:
    from vinst.launcher import port_in_use

    uninstall()
    # launchd reports success even when it can't listen, and then never starts the builder.
    deadline = time.monotonic() + 3
    while port_in_use(port):
        if time.monotonic() > deadline:
            raise OSError(f"Port {port} is in use, so macOS can't listen on it for the builder. If a builder "
                          "started by the launcher is open, close its page, wait two minutes and try again.")
        time.sleep(0.2)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plist(python, uv, repo, port, log))
    done = subprocess.run(["launchctl", "bootstrap", domain(), str(PLIST)], capture_output=True, text=True)
    if done.returncode != 0:
        PLIST.unlink()
        raise OSError(f"macOS didn't take the builder's launch agent: {done.stderr.strip()}")


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", f"{domain()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)


def activate_socket() -> int:
    """The listening socket launchd holds for this job; only the process launchd started may take it."""
    lib = ctypes.CDLL(LIBSYSTEM, use_errno=True)
    fds = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t()
    err = lib.launch_activate_socket(SOCKET.encode(), ctypes.byref(fds), ctypes.byref(count))
    if err or count.value != 1:
        raise OSError(err, f"no socket from launchd ({os.strerror(err) if err else f'{count.value} sockets'})")
    fd = fds[0]
    lib.free(fds)
    return fd


def spawn_disclaimed(argv: list[str]) -> int:
    """Starts `argv` as responsible for itself. Otherwise macOS judges the microphone by this helper's
    Python, which it refuses without asking; `uv` as its own process gets it, as when run in Terminal."""
    lib = ctypes.CDLL(LIBSYSTEM, use_errno=True)
    attr = ctypes.c_void_p()
    lib.posix_spawnattr_init(ctypes.byref(attr))
    try:
        lib.responsibility_spawnattrs_setdisclaim(ctypes.byref(attr), 1)
        args = [a.encode() for a in argv]
        env = [k + b"=" + v for k, v in os.environb.items()]
        pid = ctypes.c_int()
        err = lib.posix_spawn(ctypes.byref(pid), args[0], None, ctypes.byref(attr),
                              (ctypes.c_char_p * (len(args) + 1))(*args, None),
                              (ctypes.c_char_p * (len(env) + 1))(*env, None))
    finally:
        lib.posix_spawnattr_destroy(ctypes.byref(attr))
    if err:
        raise OSError(err, f"couldn't start {argv[0]}: {os.strerror(err)}")
    return pid.value


def serve(uv: str, port: int) -> int:
    """What launchd runs: hands its socket to the builder, then waits, so launchd leaves the port to it."""
    from vinst.launcher import trim_log

    trim_log()
    fd = activate_socket()
    os.set_inheritable(fd, True)
    pid = spawn_disclaimed([uv, "run", "--quiet", "vinst", "ui", "--fd", str(fd), "--port", str(port),
                            "--no-show", "--idle-exit", str(IDLE_EXIT_S)])
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(serve(sys.argv[1], int(sys.argv[2])))
