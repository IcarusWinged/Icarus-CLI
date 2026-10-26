"""Python-execution toggle, inline icon approval, code-block styling."""
import json

import pytest
from test_security import KEY, H, seed, srv  # noqa: F401
from textual.widgets import Button

from icarus_cli import app as app_mod
from icarus_cli import pyrunner, storage
from icarus_cli.widgets import RunCard

CODE = "print(6*7)"


class Calls:
    def __init__(self, output="42\n"):
        self.calls, self.secrets, self.output = [], [], output
        self.saved_files_dir = None

    async def __call__(self, code, timeout=20, secrets=()):
        self.calls.append(code); self.secrets.append(list(secrets))
        return pyrunner.RunResult(0, self.output, duration=0.01, saved_files_dir=self.saved_files_dir)


@pytest.fixture
def fake(monkeypatch):
    f = Calls()
    monkeypatch.setattr(app_mod.pyrunner, "run_python", f)
    return f


def log_text(app):
    return "\n".join(str(w.render()) for w in app.query("#log Static"))


def screen_text(app):
    return "\n".join("".join(s.text for s in st).rstrip() for st in app.screen._compositor.render_strips())


async def say(p, text):
    await p.click("#prompt"); await p.press(*text, "enter"); await p.pause(1.0)


async def started(srv, size=(100, 36), mode="pycode", python=True):
    seed(srv, model="m"); H.mode = mode
    app = app_mod.ChatApp()
    return app


async def press_button(p, app, bid):
    app.query(RunCard).last().query_one(f"#icon-{bid}", Button).press(); await p.pause(0.8)


# ── the toggle ───────────────────────────────────────────────────────────
async def test_python_is_off_by_default_toggles_and_is_never_persisted(srv):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause()
        assert app.python_enabled is False and "python: off" in screen_text(app)
        await say(p, "/python on")
        assert app.python_enabled and "python: ON (asks first)" in screen_text(app)
        assert "NOT a sandbox" in log_text(app)
        await p.press("f8"); await p.pause(0.2); assert not app.python_enabled           # F8 toggles
        await p.press("f8"); await p.pause(0.2); assert app.python_enabled
        await say(p, "/py off"); assert not app.python_enabled
        await say(p, "/python status"); assert "off" in log_text(app)
        await say(p, "/python on")
        assert "python" not in json.dumps(storage.load_settings()).lower()
    assert app_mod.ChatApp().python_enabled is False                                    # fresh launch = off


async def test_off_means_no_system_prompt_no_button_and_run_refuses(srv, fake):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "calc")
        assert len(app.query(".runcard")) == 0
        assert all(m["role"] != "system" for m in H.bodies[-1]["messages"])
        await say(p, "/run")
        assert "Python access is off" in log_text(app) and fake.calls == []


# ── approval flow ────────────────────────────────────────────────────────
async def test_nothing_runs_without_pressing_an_icon_and_cancel_runs_nothing(srv, fake):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        assert len(app.query(".runcard")) == 1 and fake.calls == []                      # offered, not run
        sys_msgs = [m for m in H.bodies[-1]["messages"] if m["role"] == "system"]
        assert len(sys_msgs) == 1 and "fenced code block" in sys_msgs[0]["content"]
        assert all(m["role"] != "system" for m in app.history)                           # not stored in history
        text = screen_text(app)
        assert CODE in text and "runs as you" in text                                    # exact code shown inline
        assert {"✓", "➤", "✕"} <= set(text)                                             # tiny icons, no dialog box
        await press_button(p, app, "cancel")
        assert fake.calls == [] and "Cancelled" in log_text(app)


async def test_run_private_shows_output_and_does_not_tell_the_model(srv, fake):
    fake.output = "[bold]42[/] [/oops\n"                                                  # markup in output must be inert
    fake.saved_files_dir = "/tmp/icarus-cli/files/run-example"
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        posts = len(H.bodies)
        await press_button(p, app, "private")
        assert fake.calls == [CODE]
        assert "[bold]42[/] [/oops" in log_text(app) and "exit code 0" in log_text(app)
        assert fake.saved_files_dir in log_text(app)
        assert len(H.bodies) == posts and not any(m["content"].startswith("Python output") for m in app.history)
        assert not app.query_one("#prompt").disabled


async def test_run_and_send_output_gives_the_model_the_result(srv, fake):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        await press_button(p, app, "send"); await p.pause(0.8)
        assert fake.calls == [CODE]
        sent = H.bodies[-1]["messages"][-1]
        assert sent["role"] == "user" and sent["content"].startswith("Python output") and "42" in sent["content"]
        assert app.history[-1] == {"role": "assistant", "content": "Done: it printed 42."}
        assert not app.query_one("#prompt").disabled


async def test_stored_api_keys_are_passed_to_the_runner_for_redaction(srv, fake):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        await press_button(p, app, "private")
        assert KEY in fake.secrets[0]


async def test_keyboard_shortcuts_run_the_last_block(srv, fake):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        posts = len(H.bodies)
        await p.press("ctrl+r"); await p.pause(0.8)                                      # run, keep private
        assert fake.calls == [CODE] and len(H.bodies) == posts
        await p.press("f9"); await p.pause(1.0)                                      # run, send to model (F9)
        assert fake.calls == [CODE, CODE] and len(H.bodies) == posts + 1


async def test_cards_disappear_when_python_is_switched_off_or_chat_is_reset(srv, fake):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        assert len(app.query(".runcard")) == 1
        await say(p, "/python off"); assert len(app.query(".runcard")) == 0
        await say(p, "/run"); assert fake.calls == []
        await say(p, "/python on"); await say(p, "again"); assert len(app.query(".runcard")) == 1
        await p.press("f5"); await p.pause(0.4)
        assert len(app.query(".runcard")) == 0 and app._last_block is None


@pytest.mark.parametrize("mode,n", [("pyunclosed", 0), ("pybash", 0), ("pytwo", 2)])
async def test_only_complete_python_blocks_are_offered(srv, fake, mode, n):
    app = await started(srv, mode=mode)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "go")
        assert len(app.query(".runcard")) == n and fake.calls == []


async def test_slash_parsing_does_not_swallow_normal_messages(srv, fake):
    app = await started(srv, mode="ok")
    async with app.run_test(size=(110, 36)) as p:
        await p.pause()
        await say(p, "/python bogus"); assert "Usage: /python" in log_text(app) and H.bodies == []
        await say(p, "/etc/passwd is a file"); assert len(H.bodies) == 1                 # a message, not a command
        await say(p, "/nope"); assert "Unknown command" in log_text(app) and len(H.bodies) == 1


async def test_icons_fit_a_small_terminal(srv, fake):
    app = await started(srv)
    async with app.run_test(size=(80, 24)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        for bid in ("private", "send", "cancel"):
            r = app.query(RunCard).last().query_one(f"#icon-{bid}", Button).region
            assert r.height > 0 and r.bottom <= 24 and r.right <= 80, (bid, r)


# ── real subprocess, end to end ──────────────────────────────────────────
async def test_real_run_end_to_end(srv):
    app = await started(srv)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "calc")
        await press_button(p, app, "private"); await p.pause(1.0)
        t = log_text(app)
        assert "42" in t and "exit code 0" in t


async def test_escape_stops_a_running_program_quickly(srv):
    import os
    import tempfile
    import time
    app = await started(srv, mode="pysleep")
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "/python on"); await say(p, "go")
        await press_button(p, app, "private"); await p.pause(1.0)
        assert app.query_one("#prompt").disabled                                         # busy
        t = time.monotonic(); await p.press("escape"); await p.pause(1.0)
        assert not app.query_one("#prompt").disabled and time.monotonic() - t < 4
        assert not any(d.startswith("icarus-cli-run") for d in os.listdir(tempfile.gettempdir()))


# ── code-block background ────────────────────────────────────────────────
def code_row_bgs(app):
    out = set()
    for strip in app.screen._compositor.render_strips():
        if CODE in "".join(s.text for s in strip):
            for seg in strip:
                if seg.text.strip() and seg.style and seg.style.bgcolor:
                    bg = seg.style.bgcolor
                    out.add((bg.type.name, bg.get_truecolor().hex if bg.type.name == "TRUECOLOR" else ""))
    return out


async def test_code_block_background_modes(srv):
    app = await started(srv, python=False)
    async with app.run_test(size=(110, 36)) as p:
        await p.pause(); await say(p, "calc")
        assert app.code_bg == "translucent"
        translucent = code_row_bgs(app)
        assert {t for t, _ in translucent} == {"TRUECOLOR"}                               # visibly tinted
        await say(p, "/codebg")                                                           # cycles translucent -> opaque
        assert app.code_bg == "opaque"
        opaque = code_row_bgs(app)
        assert {t for t, _ in opaque} == {"TRUECOLOR"} and opaque != translucent
        await say(p, "/codebg")                                                           # -> none
        assert app.code_bg == "none" and {t for t, _ in code_row_bgs(app)} == {"DEFAULT"}
        await say(p, "/codebg translucent"); assert code_row_bgs(app) == translucent
        await say(p, "/codebg nonsense"); assert "Usage: /codebg" in log_text(app) and app.code_bg == "translucent"
        assert storage.load_settings()["code_bg"] == "translucent"
    assert app_mod.ChatApp().code_bg == "translucent"                                    # remembered across launches
