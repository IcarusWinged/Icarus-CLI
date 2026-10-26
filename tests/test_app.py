import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from icarus_cli import app as app_mod
from icarus_cli import client, providers, screens, storage


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.path.startswith("/g/v1beta/models"):
            if self.headers["x-goog-api-key"] != "secret":
                return self._send(403, json.dumps({"error": {"code": 403, "message": "bad key", "status": "PERMISSION_DENIED"}}))
            assert "Authorization" not in self.headers
            self._send(200, json.dumps({"models": [
                {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-4-argon", "displayName": "Gemini 4 Argon", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemma-3-27b-it", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]},
            ]}))
        elif self.path.startswith("/or/v1/models"):
            if self.headers["Authorization"] != "Bearer secret":
                return self._send(401, json.dumps({"error": {"message": "bad key"}}))
            assert self.headers["X-Title"] == "icarus-cli"
            self._send(200, json.dumps({"data": [
                {"id": "meta/free-one:free", "name": "Free One", "pricing": {"prompt": "0", "completion": "0"},
                 "architecture": {"output_modalities": ["text"]}},
                {"id": "acme/paid-one", "name": "Paid One", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                 "architecture": {"output_modalities": ["text"]}},
                {"id": "acme/painter", "name": "Painter", "pricing": {"prompt": "0", "completion": "0"},
                 "architecture": {"output_modalities": ["image"]}},
            ]}))
        elif self.path.endswith("/models"):
            if self.headers["Authorization"] != "Bearer secret":
                return self._send(401, json.dumps({"error": {"message": "bad key"}}))
            self._send(200, json.dumps({"data": [{"id": "m-b"}, {"id": "m-a"}]}))

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.startswith("/g/v1beta/models/"):
            assert self.path.split("?")[0].endswith("gemini-a:streamGenerateContent"), self.path
            if self.headers["x-goog-api-key"] != "secret":
                return self._send(403, json.dumps({"error": {"code": 403, "message": "bad key", "status": "PERMISSION_DENIED"}}))
            body = json.loads(raw)
            assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
            assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
            assert body["generationConfig"]["maxOutputTokens"] == 77
            out = ""
            for t in ("Hello", " **world**"):
                out += "data: " + json.dumps({"candidates": [{"content": {"role": "model", "parts": [
                    {"text": t}, {"text": "(thinking)", "thought": True}]}}]}) + "\r\n\r\n"
            return self._send(200, out, "text/event-stream")
        if self.path.startswith("/or/") and self.headers["X-Title"] != "icarus-cli":
            return self._send(400, json.dumps({"error": {"message": "missing X-Title"}}))
        out = ""
        for t in ("Hello", " **world**"):
            out += "data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n"
        self._send(200, out + "data: [DONE]\n\n", "text/event-stream")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    patched = {
        "inception": dataclasses.replace(providers.PROVIDERS["inception"], base_url=base + "/v1", models_url=base + "/v1/models"),
        "nvidia": dataclasses.replace(providers.PROVIDERS["nvidia"], base_url=base + "/n/v1", models_url=base + "/n/v1/models"),
        "google": dataclasses.replace(providers.PROVIDERS["google"], base_url=base + "/g"),
        "openrouter": dataclasses.replace(providers.PROVIDERS["openrouter"], base_url=base + "/or/v1", models_url=base + "/or/v1/models"),
    }
    for pid, prov in patched.items():
        monkeypatch.setitem(providers.PROVIDERS, pid, prov)
    yield
    srv.shutdown()


async def ids(provider, key="secret"):
    return [m.id for m in await client.list_models(provider, key)]


async def test_client(server):
    p = providers.PROVIDERS
    assert await ids(p["inception"]) == ["m-a", "m-b"]
    assert await ids(p["nvidia"]) == ["m-a", "m-b"]
    with pytest.raises(client.APIError, match="bad key"):
        await client.list_models(p["inception"], "nope")
    text = "".join([c async for c in client.stream_chat(p["inception"], "secret", "m-a", [{"role": "user", "content": "hi"}])])
    assert text == "Hello **world**"
    assert [m.free for m in await client.list_models(p["inception"], "secret")] == [None, None]   # no free/paid info


async def test_google_connector_lists_chat_models_split_into_free_and_paid(server):
    models = await client.list_models(providers.PROVIDERS["google"], "secret")
    assert [(m.id, m.free) for m in models] == [
        ("gemini-2.5-pro", True), ("gemini-4-argon", False), ("gemma-3-27b-it", True)]   # tts + embedding models dropped
    assert models[0].name == "Gemini 2.5 Pro"
    with pytest.raises(client.APIError, match="HTTP 403: bad key"):
        await client.list_models(providers.PROVIDERS["google"], "nope")


async def test_google_connector_streams_through_the_sdk(server):
    msgs = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"}, {"role": "user", "content": "again"}]
    text = "".join([c async for c in client.stream_chat(providers.PROVIDERS["google"], "secret", "gemini-a", msgs, 77)])
    assert text == "Hello **world**"                      # the "(thinking)" part is not shown
    with pytest.raises(client.APIError, match="HTTP 403"):
        [c async for c in client.stream_chat(providers.PROVIDERS["google"], "nope", "gemini-a", msgs, 77)]


async def test_openrouter_connector_splits_free_and_paid_and_drops_non_text(server):
    models = await client.list_models(providers.PROVIDERS["openrouter"], "secret")
    assert [(m.id, m.free) for m in models] == [("acme/paid-one", False), ("meta/free-one:free", True)]
    text = "".join([c async for c in client.stream_chat(providers.PROVIDERS["openrouter"], "secret", "acme/paid-one", [{"role": "user", "content": "hi"}])])
    assert text == "Hello **world**"


def test_every_provider_has_its_own_connector():
    from icarus_cli import connectors

    assert set(connectors.CONNECTORS) == set(providers.PROVIDERS) == {"inception", "nvidia", "google", "openrouter"}


def test_keystore_roundtrip_fallback_file(tmp_path, monkeypatch, no_keyring):
    monkeypatch.setenv("LLM_USER_PATH", str(tmp_path))
    (tmp_path / "keys.json").write_text('{"openai": "sk-keep"}')
    p = providers.PROVIDERS["google"]
    storage.set_key(p, " abc12345wxyz ")
    data = json.loads((tmp_path / "keys.json").read_text())
    assert data == {"openai": "sk-keep", "gemini": "abc12345wxyz"}
    assert oct((tmp_path / "keys.json").stat().st_mode)[-3:] == "600"
    assert not storage.backend_info()[1]
    storage.set_key(p, "")
    assert "gemini" not in json.loads((tmp_path / "keys.json").read_text())


def solid_cells(app):
    """Cells painted with a real (truecolor) background. Transparent = terminal default, so this
    should only ever be the blinking text caret (1 cell)."""
    return sum(len(seg.text) for strip in app.screen._compositor.render_strips() for seg in strip
               if seg.style and seg.style.bgcolor and seg.style.bgcolor.type.name == "TRUECOLOR")


async def test_full_flow(server, tmp_path):
    from textual.widgets import Input, OptionList

    app = app_mod.ChatApp(transparent=True)
    async with app.run_test(size=(110, 36)) as pilot:
        await pilot.pause()
        # brand-new user, no keys anywhere -> key screen opens by itself
        assert isinstance(app.screen, screens.KeysScreen)
        app.screen.query_one("#key-inception", Input).value = "secret"
        app.screen.query_one("#save").press()
        await pilot.pause(0.6)
        assert storage.get_key(providers.PROVIDERS["inception"]) == "secret"
        assert not (tmp_path / "keys.json").exists() or "secret" not in (tmp_path / "keys.json").read_text()
        # ...and once a key exists the model menu opens straight away
        assert isinstance(app.screen, screens.ModelScreen)
        olist = app.screen.query_one("#models", OptionList)
        assert olist.option_count == 2
        olist.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.model == "m-a" and app.provider_id == "inception"
        # chat
        await pilot.click("#prompt")
        await pilot.press(*"hi", "enter")
        await pilot.pause(1.0)
        assert [m["role"] for m in app.history] == ["user", "assistant"]
        assert app.history[1]["content"] == "Hello **world**"
        assert not app.query_one("#prompt").disabled
        # transparency: no truecolor backgrounds anywhere
        assert solid_cells(app) <= 1, solid_cells(app)
        # theme change persists
        app.theme = "nord"
        await pilot.pause()
        assert storage.load_settings()["theme"] == "nord"
        assert solid_cells(app) <= 1, solid_cells(app)
        # new chat
        await pilot.press("f5")
        await pilot.pause()
        assert app.history == []
