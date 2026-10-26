"""Adversarial tests: hostile server, hostile input, key handling, races."""
import dataclasses
import json
import os
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from textual.widgets import Markdown

from icarus_cli import app as app_mod
from icarus_cli import client, providers, storage

KEY = "sk-SUPERSECRET-1234567890"


class H(BaseHTTPRequestHandler):
    mode = "ok"
    bodies = []          # every POSTed chat request body (parsed), newest last
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers()
        self.wfile.write(body.encode() if isinstance(body, str) else body)
    def do_GET(self):
        m = H.mode
        if m == "echo_key":
            return self._send(401, json.dumps({"error": {"message": f"Invalid key {self.headers['Authorization'].split()[-1]}"}}))
        if m == "markup_err":
            return self._send(500, "[/oops] [bold]boom")
        if m == "evil_models":
            return self._send(200, json.dumps({"data": [{"id": "[/]evil"}, {"id": "[bold"}, {"id": "ok"}]}))
        if m == "weird_models":
            return self._send(200, json.dumps({"data": [{"id": 5}, {"id": None}, {"id": "a"}, "x", {"noid": 1}]}))
        self._send(200, json.dumps({"data": [{"id": "m"}]}))
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        try: H.bodies.append(json.loads(raw))
        except ValueError: pass
        m = H.mode
        def ev(o): return "data: " + (o if isinstance(o, str) else json.dumps(o)) + "\n\n"
        if m == "echo_key":
            return self._send(401, json.dumps({"error": {"message": f"bad {self.headers['Authorization']}"}}))
        if m in ("pycode", "pysleep", "pyunclosed", "pybash", "pytwo"):
            last = (H.bodies[-1]["messages"][-1]["content"] if H.bodies else "")
            if last.startswith("Python output"):
                txt = "Done: it printed 42."
            else:
                txt = {"pycode": "Sure:\n\n```python\nprint(6*7)\n```\n",
                       "pysleep": "Here:\n\n```python\nimport time\ntime.sleep(30)\n```\n",
                       "pyunclosed": "Here:\n\n```python\nprint(1)\n",
                       "pybash": "Run this:\n\n```bash\nrm -rf ~\n```\n",
                       "pytwo": "Two:\n\n```python\nprint(1)\n```\n\nand\n\n```py\nprint(2)\n```\n"}[m]
            return self._send(200, ev({"choices": [{"delta": {"content": txt}}]}) + "data: [DONE]\n\n", "text/event-stream")
        if m == "escapes":
            body = ev({"choices": [{"delta": {"content": "hi\x1b]0;PWNED\x07\x1b]52;c;QUJD\x07\x1b[31m red\x9b31m [x](javascript:alert(1))"}}]})
        elif m == "fuzz":
            body = "".join(ev(x) for x in [
                "not json", "123", '"str"', "[]", "null", "[1,2]", {"choices": None}, {"choices": [None]},
                {"choices": ["x"]}, {"choices": [{"delta": None}]}, {"choices": [{"delta": {"content": ["l"]}}]},
                {"choices": [{"delta": {"content": 5}}]}, {"choices": [{"delta": {"content": "OK"}}]}]) + "data: [DONE]\n\n"
        elif m == "huge":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            try:
                for _ in range(400):
                    self.wfile.write(ev({"choices": [{"delta": {"content": "A" * 20000}}]}).encode())
            except OSError: pass
            return
        elif m == "slow":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            try:
                for i in range(30):
                    self.wfile.write(ev({"choices": [{"delta": {"content": f"w{i} "}}]}).encode()); self.wfile.flush(); time.sleep(0.1)
            except OSError: pass
            return
        else:
            body = ev({"choices": [{"delta": {"content": "fine"}}]}) + "data: [DONE]\n\n"
        self._send(200, body, "text/event-stream")


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{s.server_port}"
    for pid in ("inception", "nvidia", "openrouter"):
        monkeypatch.setitem(providers.PROVIDERS, pid, dataclasses.replace(
            providers.PROVIDERS[pid], base_url=base + "/v1", models_url=base + "/v1/models"))
    monkeypatch.setitem(providers.PROVIDERS, "google", dataclasses.replace(providers.PROVIDERS["google"], base_url=base + "/g"))
    H.mode = "ok"; H.bodies = []
    yield tmp_path
    s.shutdown()


P = lambda: providers.PROVIDERS["inception"]
async def chat(): return [c async for c in client.stream_chat(P(), KEY, "m", [{"role": "user", "content": "x"}])]


# ── storage ──────────────────────────────────────────────────────────────
def test_file_born_private_even_if_chmod_fails(tmp_path, monkeypatch, no_keyring):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path / "new"))
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)   # prove we don't rely on chmod-after-write
    old = os.umask(0)
    try: storage.set_key(P(), KEY)
    finally: os.umask(old)
    assert stat.S_IMODE((tmp_path / "new" / "keys.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "new").stat().st_mode) == 0o700
    assert not list((tmp_path / "new").glob("*.tmp"))

def test_corrupt_keys_file_is_never_destroyed(tmp_path, monkeypatch, no_keyring):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    (tmp_path / "keys.json").write_text('{"openai": "sk-precious",  oops')
    with pytest.raises(Exception): storage.set_key(P(), KEY)
    assert "sk-precious" in (tmp_path / "keys.json").read_text()

@pytest.mark.parametrize("bad", ["abc\r\nX-Evil: 1", "key with space", "ключ", "a\x00b", "x" * 5000])
def test_key_validation(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    with pytest.raises(ValueError): storage.set_key(P(), bad)

# ── hostile server ───────────────────────────────────────────────────────
async def test_error_never_leaks_key(srv):
    H.mode = "echo_key"
    for coro in (client.list_models(P(), KEY), chat()):
        with pytest.raises(client.APIError) as e: await coro
        assert KEY not in str(e.value) and "SUPERSECRET" not in str(e.value)

async def test_terminal_escapes_stripped(srv):
    H.mode = "escapes"
    out = "".join(await chat())
    assert not any(ord(c) < 32 and c not in "\n\t" or 0x7f <= ord(c) <= 0x9f for c in out), repr(out)

async def test_malformed_stream_does_not_crash(srv):
    H.mode = "fuzz"
    assert "".join(await chat()) == "OK"

async def test_weird_model_list(srv):
    H.mode = "weird_models"
    assert [m.id for m in await client.list_models(P(), KEY)] == ["a"]

async def test_runaway_stream_is_capped(srv):
    H.mode = "huge"   # server tries to push 8 MB
    got = []
    with pytest.raises(client.APIError, match="size limit"):
        async for c in client.stream_chat(P(), KEY, "m", [{"role": "user", "content": "x"}]):
            got.append(c)
    assert sum(map(len, got)) <= client.MAX_REPLY_CHARS

# ── UI robustness ────────────────────────────────────────────────────────
def seed(tmp_path, **s):
    storage.set_key(providers.PROVIDERS["inception"], KEY)
    (tmp_path / "icarus-cli.json").write_text(json.dumps({"provider": "inception", **s}))

async def test_markup_injection_in_models_status_and_errors(srv):
    seed(srv, model="[/]x[bold")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 30)) as p:
        await p.pause()
        for mode in ("evil_models", "markup_err"):
            H.mode = mode; app.model_cache.clear()
            await p.press("f2"); await p.pause(0.6)
            await p.press("escape"); await p.pause()
        assert app.is_running

async def test_model_output_cannot_open_links_or_inject(srv):
    seed(srv, model="m"); H.mode = "escapes"
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 30)) as p:
        await p.pause(); await p.click("#prompt"); await p.press(*"go", "enter"); await p.pause(1)
        md = app.query(Markdown).last()
        assert md._open_links is False
        assert "\x1b" not in app.history[-1]["content"]

async def test_new_chat_mid_stream_leaves_no_stale_state(srv):
    seed(srv, model="m"); H.mode = "slow"
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 30)) as p:
        await p.pause(); await p.click("#prompt"); await p.press(*"go", "enter"); await p.pause(0.6)
        await p.press("f5"); await p.pause(1.0)
        assert app.history == [], app.history
        assert not app.query_one("#prompt").disabled


async def test_crash_traceback_does_not_dump_key(srv):
    import io

    from rich.console import Console
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 30)) as p:
        await p.pause()
        def boom():
            key = KEY; secret_local = KEY  # noqa: F841
            raise RuntimeError("kaboom")
        try: boom()
        except RuntimeError: app._fatal_error()
        out = io.StringIO()
        Console(file=out, width=120).print(*app._exit_renderables)
        assert "kaboom" in out.getvalue() and "SUPERSECRET" not in out.getvalue()
        app._exit_renderables.clear()

def test_tampered_settings_file_is_harmless(srv):
    (srv / "icarus-cli.json").write_text(json.dumps({"provider": ["x"], "model": {"a": 1}, "theme": ["nord"]}))
    app_mod.ChatApp()  # must not raise
    (srv / "icarus-cli.json").write_text("[1,2,3]"); app_mod.ChatApp()
    (srv / "icarus-cli.json").write_text("\x00garbage"); app_mod.ChatApp()


async def test_random_sse_fuzz():
    import random

    import httpx
    random.seed(1337)
    junk = [b"data: ", b"\n", b"\r\n", b"{", b"}", b'"choices"', b"[", b"]", b"null", b"\x00", b"\x1b[2J", b"\xff\xfe",
            b"data: [DONE]\n", b'{"choices":[{"delta":{"content":"x"}}]}', b'{"choices":5}', b'{"choices":"abc"}', b'{"choices":{"a":1}}',
            b'{"choices":[{"delta":{"content":null}}]}', b"\xe2\x80\xae", b":", b"event: x\n"]
    real = client._http
    try:
        for _ in range(1500):
            body = b"".join(random.choice(junk) for _ in range(random.randint(0, 40)))
            client._http = lambda b=body: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b)))
            try:
                out = "".join([c async for c in client.stream_chat(P(), "k" * 20, "m", [])])
                assert not client._BAD.search(out)
            except client.APIError:
                pass
    finally:
        client._http = real

@pytest.mark.parametrize("payload", ['{"data": 5}', '{"data": "abc"}', '{"data": {"a":1}}', '{"models": 5}', '[1,2]', 'null', '"x"', ''])
async def test_model_list_garbage(payload):
    import httpx
    real = client._http
    client._http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=payload.encode())))
    try:
        for pid in ("inception", "nvidia", "openrouter"):
            try: assert await client.list_models(providers.PROVIDERS[pid], "k" * 20) == []
            except client.APIError: pass   # clean, user-visible error is fine
    finally:
        client._http = real


# ── OS keyring behaviour ─────────────────────────────────────────────────
def test_keyring_mode_never_writes_plaintext(tmp_path, monkeypatch, mem_keyring):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    storage.set_key(P(), KEY)
    assert mem_keyring.d[("icarus-cli", "inception")] == KEY
    assert not any(KEY in f.read_text() for f in tmp_path.iterdir() if f.is_file())
    assert storage.get_key(P()) == KEY and storage.key_source(P()) == "keyring"
    assert storage.backend_info()[1] is True
    storage.set_key(P(), "")
    assert storage.get_key(P()) is None and mem_keyring.d == {}

def test_legacy_plaintext_keys_migrate_and_are_scrubbed(tmp_path, monkeypatch, mem_keyring):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    (tmp_path / "keys.json").write_text(json.dumps({"inception": KEY, "gemini": "AIzaGEMINI123", "openai": "sk-keep", "nvidia": "bad key"}))
    moved = storage.migrate_legacy(providers.PROVIDERS.values())
    assert sorted(moved) == ["Google AI", "Inception Labs"]          # invalid 'nvidia' value is left alone
    left = json.loads((tmp_path / "keys.json").read_text())
    assert left == {"openai": "sk-keep", "nvidia": "bad key"}        # unrelated keys untouched
    assert mem_keyring.d[("icarus-cli", "gemini")] == "AIzaGEMINI123"
    assert storage.migrate_legacy(providers.PROVIDERS.values()) == []  # idempotent

def test_no_migration_without_keyring(tmp_path, monkeypatch, no_keyring):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    (tmp_path / "keys.json").write_text(json.dumps({"inception": KEY}))
    assert storage.migrate_legacy(providers.PROVIDERS.values()) == []
    assert KEY in (tmp_path / "keys.json").read_text()

def test_broken_keyring_does_not_silently_downgrade_to_plaintext(tmp_path, monkeypatch):
    import keyring
    from conftest import BrokenKeyring
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    keyring.set_keyring(BrokenKeyring())
    with pytest.raises(OSError, match="keyring"):
        storage.set_key(P(), KEY)
    assert not (tmp_path / "keys.json").exists()

def test_failed_migration_keeps_plaintext(tmp_path, monkeypatch):
    import keyring
    from conftest import BrokenKeyring
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    keyring.set_keyring(BrokenKeyring())
    (tmp_path / "keys.json").write_text(json.dumps({"inception": KEY}))
    assert storage.migrate_legacy(providers.PROVIDERS.values()) == []
    assert json.loads((tmp_path / "keys.json").read_text()) == {"inception": KEY}   # never lose the only copy

def test_env_var_fallback_and_priority(tmp_path, monkeypatch, mem_keyring):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path)); monkeypatch.setenv("INCEPTION_API_KEY", "env-key-123")
    assert storage.get_key(P()) == "env-key-123" and storage.key_source(P()) == "env"
    storage.set_key(P(), KEY)
    assert storage.get_key(P()) == KEY and storage.key_source(P()) == "keyring"   # keyring beats env

async def test_ui_shows_keyring_and_fallback_state(srv, no_keyring):
    from textual.widgets import Label
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 36)) as p:
        await p.pause(); await p.press("escape"); await p.press("f3"); await p.pause()
        labels = " ".join(str(l.render()) for l in app.screen.query(Label))
        assert "NO OS KEYRING" in labels

def test_migration_verifies_readback_before_deleting_plaintext(tmp_path, monkeypatch):
    """A keyring that *accepts* the write but doesn't persist it must not cost us the only copy."""
    import keyring
    from conftest import MemKeyring
    class Lossy(MemKeyring):
        def set_password(self, service, user, password): pass          # silently drops the secret
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    keyring.set_keyring(Lossy())
    (tmp_path / "keys.json").write_text(json.dumps({"inception": KEY}))
    assert storage.migrate_legacy(providers.PROVIDERS.values()) == []
    assert json.loads((tmp_path / "keys.json").read_text()) == {"inception": KEY}
