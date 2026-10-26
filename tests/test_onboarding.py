"""First-run experience, slash commands, partial-key users, key-in-chat guard."""

import pytest
from test_security import (  # noqa: F401  (mock server fixture + helpers)
    KEY,
    H,
    seed,
    srv,
)
from textual.widgets import Button, Input, Label, OptionList

from icarus_cli import app as app_mod
from icarus_cli import providers, screens, storage
from icarus_cli.widgets import PromptBox

GEMINI = "AIzaSyFAKEFAKEFAKEFAKEFAKE1234567890abc"
NVKEY = "nvapi-FAKEFAKEFAKEFAKEFAKEFAKEFAKE12345"


def log_text(app):
    return "\n".join(str(w.render()) for w in app.query("#log Static"))


async def send(pilot, text):
    await pilot.click("#prompt"); await pilot.press(*text, "enter"); await pilot.pause(0.4)


async def test_first_run_explains_how_to_get_keys_and_opens_key_screen(srv):
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await p.pause()
        assert isinstance(app.screen, screens.KeysScreen)
        shown = " ".join(str(l.render()) for l in app.screen.query(Label))
        for url in ("aistudio.google.com/apikey", "build.nvidia.com/settings/api-keys", "platform.inceptionlabs.ai/dashboard/api-keys", "openrouter.ai/keys"):
            assert url in shown
        assert "One key is enough" in shown
        app.screen.query_one("#cancel").press(); await p.pause()
        text = log_text(app)
        assert "at least ONE provider" in text and "/keys" in text and "aistudio.google.com/apikey" in text


async def test_user_with_only_an_nvidia_key_never_needs_google(srv):
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await p.pause()
        app.screen.query_one("#key-nvidia", Input).value = NVKEY      # google + inception left blank
        app.screen.query_one("#save").press(); await p.pause(0.8)
        assert isinstance(app.screen, screens.ModelScreen)
        assert app.provider_id == "nvidia"                              # jumped to the provider they actually have
        plist = app.screen.query_one("#providers", OptionList)
        labels = [str(plist.get_option_at_index(i).prompt) for i in range(plist.option_count)]
        assert "(no key)" in labels[0] and "(no key)" in labels[2] and "(no key)" not in labels[1]
        assert app.screen.query_one("#models", OptionList).option_count == 1   # mock NVIDIA returns one model
        # picking the keyless Google entry offers to add its key instead of failing
        plist.highlighted = 2; await p.pause(0.3); plist.focus(); await p.press("enter"); await p.pause(0.4)
        assert isinstance(app.screen, screens.KeysScreen)
        msg = " ".join(str(l.render()) for l in app.screen.query(Label))
        assert "aistudio.google.com/apikey" in msg


async def test_model_menu_hint_for_keyless_provider(srv):
    storage.set_key(providers.PROVIDERS["nvidia"], NVKEY)
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 34)) as p:
        await p.pause()
        assert isinstance(app.screen, screens.ModelScreen)
        app.screen.query_one("#providers", OptionList).highlighted = 2
        await p.pause(0.4)
        assert "No API key for Google AI" in str(app.screen.query_one("#msg", Label).render())


@pytest.mark.parametrize("size", [(80, 24), (70, 20)])
async def test_key_screen_fits_small_terminals(srv, size):
    app = app_mod.ChatApp()
    async with app.run_test(size=size) as p:
        await p.pause()
        save = app.screen.query_one("#save", Button)
        r = save.region
        assert r.bottom <= size[1] and r.right <= size[0] and r.height > 0, (r, size)


async def test_slash_commands_open_screens_and_never_reach_the_model(srv):
    seed(srv, model="m")
    posts = []
    orig = H.do_POST; H.do_POST = lambda self: (posts.append(1), orig(self))[1]
    try:
        app = app_mod.ChatApp()
        async with app.run_test(size=(100, 34)) as p:
            await p.pause()
            await send(p, "/keys"); assert isinstance(app.screen, screens.KeysScreen)
            await p.press("escape"); await p.pause()
            await send(p, "/model"); assert isinstance(app.screen, screens.ModelScreen)
            await p.press("escape"); await p.pause()
            await send(p, "/help"); assert "/keys" in log_text(app) and "/theme" in log_text(app)
            await send(p, "/bogus"); assert "Unknown command" in log_text(app)
            await send(p, "/new"); assert app.history == []
            assert posts == [] and not app.query_one("#prompt").disabled
    finally:
        H.do_POST = orig


async def test_pasting_a_key_into_chat_is_blocked_not_sent_or_logged(srv):
    seed(srv, model="m")
    posts = []
    orig = H.do_POST; H.do_POST = lambda self: (posts.append(1), orig(self))[1]
    try:
        app = app_mod.ChatApp()
        async with app.run_test(size=(100, 34)) as p:
            await p.pause()
            for secret in (GEMINI, NVKEY, KEY):          # known prefixes + a key that is already stored
                await send(p, secret)
                assert isinstance(app.screen, screens.KeysScreen), secret
                await p.press("escape"); await p.pause()
                assert app.history == [] and posts == []
                assert secret not in log_text(app)
                assert app.query_one("#prompt", PromptBox).text == ""
            assert "NOT sent" in log_text(app)
            await send(p, "my favourite number is 42")    # normal text still goes through
            assert [m["role"] for m in app.history][:1] == ["user"]
    finally:
        H.do_POST = orig


def test_looks_like_key_has_no_false_positives_on_normal_prose():
    P = providers.PROVIDERS.values()
    for text in ("hello", "explain sk-learn pipelines", "What is AIza?", "nvapi- is a prefix", "a" * 40, "/etc/passwd"):
        assert not storage.looks_like_key(text, P), text
    assert storage.looks_like_key(GEMINI, P) and storage.looks_like_key(NVKEY, P)


def screen_text(app):
    return "\n".join("".join(s.text for s in strip) for strip in app.screen._compositor.render_strips())


@pytest.mark.parametrize("size", [(80, 24), (70, 20)])
async def test_key_screen_text_is_complete_not_clipped(srv, size):
    app = app_mod.ChatApp()
    async with app.run_test(size=size) as p:
        await p.pause()
        text = screen_text(app)
        for needle in ("platform.inceptionlabs.ai/dashboard/api-keys", "build.nvidia.com/settings/api-keys",
                       "aistudio.google.com/apikey", "Save", "Cancel", "Clear"):
            assert needle in text, (needle, size)


async def test_status_bar_is_actually_visible_and_flags_missing_key(srv):
    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(100, 30)) as p:
        await p.pause()
        assert "Inception Labs · m" in screen_text(app)
        storage.set_key(providers.PROVIDERS["inception"], "")        # key removed -> status says so
        app._update_status(); await p.pause()
        assert "no API key" in screen_text(app)


async def test_name_screen_then_icon_until_first_message(srv):
    from icarus_cli import screens

    seed(srv, model="m")
    app = app_mod.ChatApp(ask_name=True)
    async with app.run_test(size=(100, 30)) as p:
        await p.pause()
        assert isinstance(app.screen, screens.NameScreen)
        await p.press("enter"); await p.pause()
        assert isinstance(app.screen, screens.NameScreen)                      # empty name is refused
        await p.press(*"neo", "enter"); await p.pause()
        assert not isinstance(app.screen, screens.NameScreen) and app.username == "neo"
        assert app.query_one("#splash").display and not app.query_one("#log").display
        await p.click("#prompt"); await p.press(*"hi", "enter"); await p.pause(1.0)
        assert not app.query_one("#splash").display and app.query_one("#log").display
        assert "neo › hi" in "\n".join(str(w.render()) for w in app.query("#log Static"))
    again = app_mod.ChatApp(ask_name=True)
    async with again.run_test(size=(100, 30)) as p:
        await p.pause()
        assert not isinstance(again.screen, screens.NameScreen) and again.username == "neo"


async def test_logo_scales_and_model_is_told_the_users_name(srv):
    from icarus_cli import logo

    assert [len(logo.render(c).plain.split("\n")[0]) for c in (30, 60, 100)] == [30, 60, 100]
    seed(srv, model="m")
    storage.save_settings(username="Neo")
    app = app_mod.ChatApp(ask_name=True)
    async with app.run_test(size=(160, 40)) as p:
        await p.pause()
        assert app.query_one("#logo").region.width <= 110                       # centred column, capped
        assert app.query_one("#shell").region.x > 0                             # blank space either side
        await p.click("#prompt"); await p.press(*"hi", "enter"); await p.pause(1.0)
        sys_msgs = [m for m in H.bodies[-1]["messages"] if m["role"] == "system"]
        assert len(sys_msgs) == 1 and "Neo" in sys_msgs[0]["content"]
        assert all(m["role"] != "system" for m in app.history)
        await p.click("#prompt"); await p.press(*"/name", "enter"); await p.pause(0.4)
        assert isinstance(app.screen, screens.NameScreen)
        await p.press(*"Trinity", "enter"); await p.pause(0.4)
        assert app.username == "Trinity" and storage.load_settings()["username"] == "Trinity"


async def test_model_menu_shows_free_and_paid_models_separately(srv):
    from icarus_cli.connectors import ModelInfo

    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(110, 36)) as p:
        await p.pause()
        app.model_cache["inception"] = [ModelInfo("paid-b", free=False), ModelInfo("free-a", "Free A", True), ModelInfo("free-c", free=True)]
        await p.press("f2"); await p.pause(0.6)
        olist = app.screen.query_one("#models", OptionList)
        rows = [(o.id, str(o.prompt)) for o in (olist.get_option_at_index(i) for i in range(olist.option_count))]
        assert [r[0] for r in rows] == [None, "free-a", "free-c", None, "paid-b"]
        assert "Free (2)" in rows[0][1] and "Paid (1)" in rows[3][1] and "Free A" in rows[1][1]
        assert olist.highlighted == 1                                       # skips the section header
        await p.press("down", "down", "down"); await p.pause(0.2)          # header is skipped again
        assert olist.highlighted == 4
        await app.screen.query_one("#filter", Input).focus()
        await p.press(*"paid"); await p.pause(0.3)
        assert [olist.get_option_at_index(i).id for i in range(olist.option_count)] == [None, "paid-b"]


async def test_model_menu_shows_free_and_paid_models_separately(srv):
    from icarus_cli.connectors import ModelInfo

    seed(srv, model="m")
    app = app_mod.ChatApp()
    async with app.run_test(size=(110, 36)) as p:
        await p.pause()
        app.model_cache["inception"] = [ModelInfo("paid-b", free=False), ModelInfo("free-a", "Free A", True), ModelInfo("free-c", free=True)]
        await p.press("f2"); await p.pause(0.6)
        olist = app.screen.query_one("#models", OptionList)
        rows = [(o.id, str(o.prompt)) for o in (olist.get_option_at_index(i) for i in range(olist.option_count))]
        assert [r[0] for r in rows] == [None, "free-a", "free-c", None, "paid-b"]
        assert "Free (2)" in rows[0][1] and "Paid (1)" in rows[3][1] and "Free A" in rows[1][1]
        assert olist.highlighted == 1                                       # skips the section header
        app.screen.query_one("#filter", Input).focus()
        await p.press(*"paid"); await p.pause(0.3)
        assert [olist.get_option_at_index(i).id for i in range(olist.option_count)] == [None, "paid-b"]
