"""Multi-line prompt box."""
from test_security import KEY, H, seed, srv  # noqa: F401
from textual import events

from icarus_cli import app as app_mod
from icarus_cli.widgets import PromptBox


def box(app):
    return app.query_one("#prompt", PromptBox)


async def start(app, p):
    await p.pause()
    await p.click("#prompt")


async def test_enter_sends_and_ctrl_j_adds_a_line(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        await p.press("h", "i", "ctrl+j", "t", "h", "e", "r", "e", "alt+enter", "x")
        assert box(app).text == "hi\nthere\nx"            # newline keys never submit
        assert app.history == []
        await p.press("enter"); await p.pause(1.0)
        assert app.history[0] == {"role": "user", "content": "hi\nthere\nx"}   # whole message, all lines
        assert box(app).text == "" and not box(app).disabled


async def test_pasting_multiple_lines_keeps_every_line(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        text = "line one\nline two\n\n    indented three\nline five"
        app.post_message(events.Paste(text)); await p.pause(0.3)
        assert box(app).text == text
        await p.press("enter"); await p.pause(1.0)
        assert app.history[0]["content"] == text


async def test_box_grows_then_scrolls_and_still_sends_everything(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        h0 = box(app).region.height
        app.post_message(events.Paste("a\nb\nc\nd\ne")); await p.pause(0.3)
        h5 = box(app).region.height
        assert h5 > h0                                   # grew with the content
        many = "\n".join(f"row {i}" for i in range(80))
        box(app).clear(); app.post_message(events.Paste(many)); await p.pause(0.4)
        assert box(app).region.height <= 12              # capped...
        assert box(app).max_scroll_y > 0                 # ...so it scrolls
        assert app.query_one("#splash").region.height >= 10  # content area is not squeezed out
        await p.press("enter"); await p.pause(1.0)
        assert app.history[0]["content"].count("\n") == 79


async def test_multiline_message_is_displayed_with_all_lines(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        app.post_message(events.Paste("first\nsecond")); await p.pause(0.2)
        await p.press("enter"); await p.pause(1.0)
        shown = "\n".join(str(w.render()) for w in app.query("#log .user"))
        assert "you › first" in shown and "second" in shown


async def test_overlong_message_is_refused_without_losing_it(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        box(app).text = "x" * (app_mod.MAX_INPUT_CHARS + 1)
        await p.press("enter"); await p.pause(0.4)
        assert app.history == [] and box(app).text == "x" * (app_mod.MAX_INPUT_CHARS + 1)   # kept exactly, no stray newline
        assert "too long" in "\n".join(str(w.render()) for w in app.query("#log Static"))


async def test_pasted_control_characters_are_stripped_before_sending(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        app.post_message(events.Paste("hello\x1b]0;evil\x07 world\r\nnext")); await p.pause(0.2)
        await p.press("enter"); await p.pause(1.0)
        sent = app.history[0]["content"]
        assert "\x1b" not in sent and "\x07" not in sent and "\r" not in sent and "hello" in sent and "next" in sent


async def test_slash_commands_and_key_guard_still_work_with_multiline_box(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        await p.press("/", "h", "e", "l", "p", "enter"); await p.pause(0.3)
        assert box(app).text == "" and app.history == []
        app.post_message(events.Paste("explain\n/keys")); await p.pause(0.2)   # multi-line text starting normally is NOT a command
        await p.press("enter"); await p.pause(1.0)
        assert app.history[0]["content"] == "explain\n/keys"


async def test_prompt_box_keeps_terminal_background_transparent(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp(transparent=True)
    async with app.run_test(size=(100, 34)) as p:
        await start(app, p)
        app._enter_chat(); await p.pause(0.1)                          # the logo art itself uses colour cells
        app.post_message(events.Paste("one\ntwo\nthree\n" + "wrap " * 40)); await p.pause(0.3)
        await p.press("ctrl+a", "shift+down", "shift+down")           # cursor line + a selection on screen
        await p.pause(0.2)
        solid = sum(len(seg.text) for strip in app.screen._compositor.render_strips() for seg in strip
                    if seg.style and seg.style.bgcolor and seg.style.bgcolor.type.name == "TRUECOLOR")
        # the only solid-coloured cell allowed is the text caret itself; everything else is the terminal default
        assert solid <= 1, solid
