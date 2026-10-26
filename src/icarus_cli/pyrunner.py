"""Run model-written Python in a throw-away subprocess.

IMPORTANT: this is *containment*, not a security sandbox. The code still runs as
your OS user and can use the network and read your files. The real safeguard is
that every run is shown to you and needs your explicit approval (see app.py).

What it does to limit the damage of a mistake or a hostile snippet:
  * `python -I`     : isolated mode; PYTHONPATH/user-site ignored, but the
                      interpreter's own venv site-packages ARE importable
  * minimal environment: no API keys / tokens / HOME; fresh empty temp dir as cwd
  * stdin closed, wall-clock timeout, output cap, process-group kill
  * POSIX rlimits (address space, CPU time, file size, no core dumps)
  * any stored API key (and trivial re-encodings of it) is redacted from output
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import client

MAX_CODE_CHARS = 50_000
TIMEOUT = 20.0  # seconds, wall clock
GUI_TIMEOUT = 600.0  # a run that opens a window (plt.show(), ...) may stay open this long; Esc stops it
MAX_CAPTURE = 1_000_000  # bytes read from the program before it is killed
HEAD, TAIL = 5_000, 3_000  # characters shown / sent back (head + tail of long output)
MEM_BYTES = 1 << 30  # 1 GiB address space
FSIZE_BYTES = 50 << 20  # largest file it may write
LANGS = {"python", "py", "python3"}

SYSTEM_PROMPT = (
    "You can run Python 3 code on the user's computer. To run code, put it in a fenced code block "
    "tagged python. The user is shown the code and must approve every run; they may decline. If they "
    "approve and choose to share it, the program's output is sent back to you in the next message.\\n"
    "Packages already installed in this environment (numpy, pandas, requests, ...) are importable; "
    "you cannot pip install anything. No input()/stdin; about 20 seconds (up to 10 minutes while a window "
    "is open); about 1 GB of memory; it runs in an empty temporary directory with no credentials. "
    "Windows work: plt.show() opens a real interactive matplotlib window on the user's screen (zoom, pan, save "
    "buttons) and the run lasts until they close it, so call plt.show() to display a figure. "
    "Files you create in the working directory are saved after the run; the app shows their destination. "
    "Use print() to show results. Keep programs short and self-contained. Never try to read credentials, "
    "API keys, or files outside the temporary directory, and never hide what your code does."
).replace("\\n", "\n")

_BOOTSTRAP = r"""
import sys
path, mem, fsize, cpu = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
try:
    import resource
    for name, val in (("RLIMIT_AS", mem), ("RLIMIT_FSIZE", fsize), ("RLIMIT_CPU", cpu), ("RLIMIT_CORE", 0)):
        lim = getattr(resource, name, None)
        if lim is not None and (val or name == "RLIMIT_CORE"):
            try:
                resource.setrlimit(lim, (val, val))
            except (ValueError, OSError):
                pass
except ImportError:
    pass
with open(path, encoding="utf-8") as fh:
    src = fh.read()
gui = len(sys.argv) > 5 and sys.argv[5] == "1"
sys.argv = ["snippet.py"]
if gui and "matplotlib" in src:
    import os
    if sys.platform in ("win32", "darwin") or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        try:
            os.environ.setdefault("QT_API", "pyside6")
            import matplotlib
            matplotlib.use("QtAgg")
        except Exception:
            pass
exec(compile(src, "snippet.py", "exec"), {"__name__": "__main__", "__file__": "snippet.py"})
"""

_GUI = re.compile(r"\.show\s*\(|tkinter|turtle|pygame|PyQt|PySide|imshow|mainloop", re.I)
_DISPLAY_VARS = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS",
)


def wants_window(code: str) -> bool:
    """True if the snippet looks like it opens a GUI window (plt.show(), tkinter, ...)."""
    return bool(_GUI.search(code))


# a closed ``` fence only; an unterminated block is never offered for execution
_FENCE = re.compile(r"(?ms)^[ \t]{0,3}```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)^[ \t]{0,3}```[ \t]*$")


def extract_python_blocks(text: str) -> list[str]:
    """Return the code of every closed ```python / ```py / ```python3 block."""
    out = []
    for m in _FENCE.finditer(text):
        if m.group(1).lower() in LANGS:
            code = m.group(2).rstrip("\n")
            if code.strip():
                out.append(code)
    return out


def secret_variants(secret: str) -> set[str]:
    """The secret plus trivial re-encodings a snippet might print instead of the raw value."""
    raw = secret.encode()
    v = {secret, secret[::-1], raw.hex(), raw.hex().upper()}
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        b = enc(raw).decode()
        v |= {b, b.rstrip("=")}
    return {x for x in v if len(x) >= 8}


def redact_secrets(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s:
            for variant in sorted(secret_variants(s), key=len, reverse=True):
                text = text.replace(variant, "[redacted]")
    return text


@dataclass
class RunResult:
    returncode: int | None
    output: str
    timed_out: bool = False
    truncated: bool = False
    duration: float = 0.0
    saved_files_dir: str | None = None
    file_save_error: str | None = None

    def summary(self) -> str:
        if self.timed_out:
            return f"timed out after {self.duration:.0f}s (killed)"
        if self.truncated:
            return "output limit exceeded (killed)"
        return f"exit code {self.returncode} in {self.duration:.2f}s"


def _clean_env(tmp: str, gui: bool = False) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": tmp,
        "TMPDIR": tmp,
        "TEMP": tmp,
        "TMP": tmp,
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",

    }
    if gui:  # only the display variables, so windows can open
        env.update({k: os.environ[k] for k in _DISPLAY_VARS if k in os.environ})
    if os.name == "nt":
        env["PATH"] = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        env["USERPROFILE"] = tmp
    return env


def _files_root() -> Path:
    """Return the per-user application-data directory for generated files."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "icarus-cli" / "files"


def _save_run_files(tmp: str) -> tuple[str | None, str | None]:
    files: list[tuple[str, str]] = []
    for root, dirs, names in os.walk(tmp, followlinks=False):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
        for name in names:
            source = os.path.join(root, name)
            try:
                if stat.S_ISREG(os.stat(source, follow_symlinks=False).st_mode):
                    files.append((source, os.path.relpath(source, tmp)))
            except FileNotFoundError:
                continue
    if not files:
        return None, None

    run_dir: str | None = None
    try:
        root = _files_root()
        root.mkdir(parents=True, exist_ok=True)
        run_dir = tempfile.mkdtemp(prefix="run-", dir=root)
        for source, relative in files:
            target = os.path.join(run_dir, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
        return run_dir, None
    except OSError as exc:
        if run_dir is not None:
            shutil.rmtree(run_dir, ignore_errors=True)
        return None, f"Could not save generated files: {exc}"


def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill the whole process group/tree, not just the interpreter."""
    try:
        if os.name == "posix":
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5)
    except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


def _shorten(text: str) -> tuple[str, bool]:
    if len(text) <= HEAD + TAIL:
        return text, False
    omitted = len(text) - HEAD - TAIL
    return f"{text[:HEAD]}\n…[{omitted:,} characters omitted]…\n{text[-TAIL:]}", True


async def run_python(code: str, timeout: float | None = None, secrets: Iterable[str] = ()) -> RunResult:
    """Run in isolated mode; PYTHONPATH/user-site are ignored, but the interpreter's own venv site-packages are importable."""
    code = client.clean(code)
    if len(code) > MAX_CODE_CHARS:
        raise ValueError(f"code is too long ({len(code):,} characters; limit {MAX_CODE_CHARS:,})")
    if not sys.executable:
        raise RuntimeError("cannot locate the Python interpreter")
    gui = wants_window(code)
    if timeout is None:
        timeout = GUI_TIMEOUT if gui else TIMEOUT
    tmp = tempfile.mkdtemp(prefix="icarus-cli-run-")
    work, home = os.path.join(tmp, "work"), os.path.join(tmp, "home")  # home holds caches (matplotlib json...), never saved
    os.makedirs(work)
    os.makedirs(home)
    start = time.monotonic()
    proc = None
    timed_out = capped = False
    buf = bytearray()
    try:
        path = os.path.join(tmp, "snippet.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(code)
        args = [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-c",
            _BOOTSTRAP,
            path,
            "0" if gui else str(MEM_BYTES),  # GUI toolkits need more address space than 1 GiB
            str(FSIZE_BYTES),
            str(int(timeout) + 5),
            "1" if gui else "0",
        ]
        kwargs: dict = dict(
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=work,
            env=_clean_env(home, gui),
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True  # own process group so we can kill children too
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = await asyncio.create_subprocess_exec(*args, **kwargs)

        async def pump() -> None:
            nonlocal capped
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    return
                buf.extend(chunk)
                if len(buf) >= MAX_CAPTURE:
                    capped = True
                    return

        try:
            await asyncio.wait_for(pump(), timeout)
            if not capped:
                await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            if timed_out or capped or proc.returncode is None:
                _kill(proc)  # also runs when the task is cancelled (Esc)
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            pass
        text = bytes(buf[:MAX_CAPTURE]).decode("utf-8", "replace")
        text = redact_secrets(client.clean(text), secrets)
        text, short = _shorten(text)
        saved_files_dir, file_save_error = _save_run_files(work)
        return RunResult(
            proc.returncode, text, timed_out, capped, time.monotonic() - start, saved_files_dir, file_save_error
        )
    finally:
        if proc is not None and proc.returncode is None:
            _kill(proc)
        shutil.rmtree(tmp, ignore_errors=True)
