"""Adversarial tests for the Python runner (real subprocesses)."""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from icarus_cli import pyrunner as r

LINUX = sys.platform.startswith("linux")
posix = pytest.mark.skipif(os.name != "posix", reason="POSIX only")


async def run(code, **kw):
    return await r.run_python(code, **{"timeout": 3, **kw})


async def test_basic_output_exit_code_and_traceback():
    res = await run('print("hello", 6*7)')
    assert res.returncode == 0 and res.output.strip() == "hello 42" and not res.timed_out
    res = await run("import sys\nprint('out')\nsys.stderr.write('err\\n')\nraise SystemExit(3)")
    assert res.returncode == 3 and "out" in res.output and "err" in res.output
    res = await run("x = 1\nprint(1/0)")
    assert res.returncode == 1 and "ZeroDivisionError" in res.output and "snippet.py" in res.output and "line 2" in res.output


async def test_environment_is_scrubbed_of_every_secret(monkeypatch):
    for k in ("GEMINI_API_KEY", "INCEPTION_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(k, "supersecretvalue-" + k)
    res = await run("import os\nprint(dict(os.environ))")
    assert "supersecret" not in res.output
    names = eval(res.output.strip())
    assert set(names) <= {"HOME", "LANG", "MPLBACKEND", "PATH", "PYTHONDONTWRITEBYTECODE", "TEMP", "TMP", "TMPDIR", "SYSTEMROOT", "USERPROFILE"}


async def test_can_import_installed_packages():
    res = await run("import keyring")
    assert res.returncode == 0, res.output
    assert (await run("import json, re, math, datetime, sqlite3, urllib.request; print('ok')")).output.strip() == "ok"


async def test_runs_in_an_empty_temp_dir_that_is_deleted_afterwards(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = await run("import os\nopen('note.txt','w').write('x')\nprint(os.getcwd(), os.listdir('.'))")
    cwd = res.output.split()[0]
    assert str(tmp_path) not in res.output and "note.txt" in res.output
    assert not os.path.exists(cwd)                       # cleaned up
    assert list(tmp_path.iterdir()) == []                # nothing leaked into the caller's cwd


async def test_generated_files_are_saved_outside_the_temporary_directory(tmp_path, monkeypatch):
    files_root = tmp_path / "saved-files"
    monkeypatch.setattr(r, "_files_root", lambda: files_root)
    res = await run("from pathlib import Path\nPath('sample_plot.svg').write_text('<svg/>')")
    assert res.returncode == 0
    assert res.saved_files_dir is not None
    assert (Path(res.saved_files_dir) / "sample_plot.svg").read_text() == "<svg/>"
    assert not (Path(res.saved_files_dir) / "snippet.py").exists()


async def test_stdin_is_closed_so_input_cannot_hang():
    t = time.monotonic()
    res = await run("input('?')")
    assert "EOFError" in res.output and time.monotonic() - t < 3


async def test_infinite_loop_is_killed_at_the_timeout():
    t = time.monotonic()
    res = await run("while True: pass", timeout=1.5)
    assert res.timed_out and time.monotonic() - t < 6


def _alive(pid: int) -> bool:
    """True only if the process is really running (a dead-but-unreaped zombie does not count)."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        pass
    except OSError:
        return False
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


@posix
async def test_child_processes_are_killed_too_not_orphaned():
    code = ("import subprocess, time\n"
            "p = subprocess.Popen(['sleep', '60'])\n"
            "print(p.pid, flush=True)\n"
            "time.sleep(60)\n")
    res = await run(code, timeout=1.5)
    pid = int(res.output.split()[0])
    await asyncio.sleep(0.7)
    alive = _alive(pid)
    if alive:
        os.kill(pid, 9)      # don't leave it behind if this test fails
    assert not alive, "child process survived the timeout"


async def test_output_flood_is_capped_and_the_process_killed():
    t = time.monotonic()
    res = await run("import sys\nwhile True:\n    sys.stdout.write('A'*100000)\n", timeout=15)
    assert res.truncated and len(res.output) <= r.HEAD + r.TAIL + 100 and time.monotonic() - t < 10


async def test_long_output_keeps_head_and_tail_and_says_so():
    res = await run("print('START'); print('x'*30000); print('END')")
    assert res.output.startswith("START") and res.output.rstrip().endswith("END") and "characters omitted" in res.output


@pytest.mark.skipif(not LINUX, reason="RLIMIT_AS is enforced on Linux")
async def test_memory_bomb_is_refused_by_the_rlimit():
    res = await run("x = bytearray(4 * 1024**3)\nprint('allocated')", timeout=10)
    assert "allocated" not in res.output and res.returncode != 0


@pytest.mark.skipif(not LINUX, reason="RLIMIT_FSIZE is enforced on POSIX")
async def test_disk_fill_is_limited():
    res = await run("f = open('big', 'wb')\nf.write(b'0' * (200 * 1024 * 1024))\nprint('written')", timeout=10)
    assert "written" not in res.output and res.returncode != 0


async def test_stored_keys_are_redacted_including_trivial_encodings():
    import base64
    key = "AIzaSyFAKEKEY-1234567890abcdef"
    code = (f"import base64\nk = {key!r}\nprint(k)\nprint(k[::-1])\nprint(base64.b64encode(k.encode()).decode())\n"
            "print(k.encode().hex())\nprint('keep this line')")
    res = await run(code, secrets=[key])
    assert key not in res.output and key[::-1] not in res.output and "keep this line" in res.output
    assert base64.b64encode(key.encode()).decode() not in res.output and key.encode().hex() not in res.output
    assert res.output.count("[redacted]") == 4


async def test_key_split_by_control_characters_is_still_redacted():
    key = "sk-SUPERSECRET-1234567890"
    res = await run("print('sk-SUPER\\x00SECRET-12\\x1b34567890')", secrets=[key])
    assert key not in res.output and "SUPERSECRET-1234567890" not in res.output


async def test_terminal_escapes_in_output_are_stripped():
    res = await run("print('a\\x1b]0;PWNED\\x07b\\x1b[31mc\\x9bd')")
    assert not any(ord(c) < 32 and c not in "\n\t" or 0x7f <= ord(c) <= 0x9f for c in res.output)


async def test_oversized_code_is_rejected_before_anything_runs():
    with pytest.raises(ValueError):
        await r.run_python("#" * (r.MAX_CODE_CHARS + 1))


async def test_cancelling_kills_the_process():
    code = "import os, time\nprint(os.getpid(), flush=True)\ntime.sleep(60)\n"
    task = asyncio.create_task(r.run_python(code, timeout=30))
    await asyncio.sleep(1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    assert not any("icarus-cli-run" in p for p in _tmpdirs())


def _tmpdirs():
    import tempfile
    return os.listdir(tempfile.gettempdir())


# ── code-block extraction ────────────────────────────────────────────────
def test_extract_blocks():
    t = "a\n```python\nprint(1)\n```\ntext\n```py\nprint(2)\n```\n```Python3\nprint(3)\n```\n```bash\nrm -rf /\n```\n```\nplain\n```\n"
    assert r.extract_python_blocks(t) == ["print(1)", "print(2)", "print(3)"]

@pytest.mark.parametrize("text", [
    "```python\nprint(1)\n",                        # never closed (stream cut off)
    "~~~python\nprint(1)\n~~~\n",                   # tilde fences are not offered
    "```bash\npython -c 'print(1)'\n```\n",
    "```python\n\n   \n```\n",                      # empty
    "inline ```python print(1)``` here",
    "```pythonic\nprint(1)\n```\n",
    "```javascript\n```python\nx\n```\n",
])
def test_extract_ignores_non_runnable(text):
    assert r.extract_python_blocks(text) == []

def test_extract_unclosed_python_does_not_swallow_following_blocks():
    # "```bash" is not a valid closing fence, so (as in CommonMark) the python block runs to the final ```
    assert r.extract_python_blocks("```python\nA\n```bash\nB\n```\n") == ["A\n```bash\nB"]


async def test_cache_files_in_home_are_not_saved_as_run_files(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "_files_root", lambda: tmp_path / "saved")
    code = (
        "import os, pathlib\n"
        "pathlib.Path(os.environ['HOME'], 'fontlist.json').write_text('{}')\n"
        "open('plot.svg', 'w').write('<svg/>')\n"
    )
    res = await r.run_python(code)
    names = sorted(p.name for p in Path(res.saved_files_dir).rglob("*") if p.is_file())
    assert names == ["plot.svg"]


async def test_window_runs_get_display_vars_and_a_long_timeout(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":42")
    assert r.wants_window("import matplotlib.pyplot as plt\nplt.show()") and not r.wants_window("print(1)")
    res = await r.run_python("import os\nprint(os.environ.get('DISPLAY'))\n# plt.show()")
    assert ":42" in res.output
    res = await r.run_python("import os\nprint(os.environ.get('DISPLAY'))")
    assert "None" in res.output


async def test_matplotlib_uses_the_pyside6_qt_backend_for_window_runs(monkeypatch):
    pytest.importorskip("matplotlib")
    pytest.importorskip("PySide6")
    original = r._clean_env

    def env(tmp, gui=False):
        e = original(tmp, gui)
        e.update({"QT_QPA_PLATFORM": "offscreen", "DISPLAY": ":0"})
        return e

    monkeypatch.setattr(r, "_clean_env", env)
    res = await r.run_python("import matplotlib.pyplot as plt\nprint(plt.get_backend())\n# plt.show()")
    assert res.returncode == 0 and "qtagg" in res.output.lower(), res.output
    res = await r.run_python("import matplotlib.pyplot as plt\nprint(plt.get_backend())")   # no show(): stays headless
    assert "qtagg" not in res.output.lower()
