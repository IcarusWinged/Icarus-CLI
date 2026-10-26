"""icarus-cli: a small, restricted chat UI for Inception Labs, NVIDIA and Google AI."""

from __future__ import annotations

import argparse

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Markdown, Static

from . import __version__, client, pyrunner, storage
from .providers import PROVIDERS
from .screens import KeysScreen, ModelScreen, NameScreen
from .connectors import ModelInfo
from .logo import Logo
from .widgets import PromptBox, RunCard

MAX_HISTORY = 20  # messages sent back to the model (keeps requests small)
MAX_TOKENS = 2048  # hard cap per reply
MAX_INPUT_CHARS = 100_000  # longest message you can send
MAX_RUN_CARDS = 5  # code blocks offered per reply
CODE_BGS = ("none", "translucent", "opaque")
KNOWN_COMMANDS = {
    "/keys",
    "/key",
    "/model",
    "/models",
    "/theme",
    "/name",
    "/new",
    "/clear",
    "/help",
    "/?",
    "/quit",
    "/exit",
    "/python",
    "/py",
    "/run",
    "/codebg",
}

ICARUS = Theme(
    name="icarus", primary="#8b6f1f", secondary="#c9b87a", accent="#e8d9a0",
    warning="#e0af68", error="#f7768e", success="#9ece6a",
    foreground="#d4c9a8", background="#0d0b07", surface="#0d0b07", panel="#1a1610", dark=True,
)
GLASS = Theme(
    name="glass",
    primary="#7dcfff",
    secondary="#bb9af7",
    accent="#9ece6a",
    warning="#e0af68",
    error="#f7768e",
    success="#9ece6a",
    foreground="#c0caf5",
    background="#1a1b26",
    surface="#1a1b26",
    panel="#24283b",
    dark=True,
)
EMBER = Theme(
    name="ember",
    primary="#ff9e64",
    secondary="#e0af68",
    accent="#ffc777",
    warning="#e0af68",
    error="#ff757f",
    success="#c3e88d",
    foreground="#e8d9c5",
    background="#1c1411",
    surface="#1c1411",
    panel="#2a1f1a",
    dark=True,
)


class ChatApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "icarus-cli"
    BINDINGS = [
        Binding("f2,ctrl+o", "models", "Models"),
        Binding("f3,ctrl+g", "keys", "API keys"),
        Binding("f4,ctrl+t", "change_theme", "Theme"),
        Binding("f5,ctrl+n", "new_chat", "New chat"),
        Binding("f8", "toggle_python", "Python on/off"),
        Binding("ctrl+r", "run_code", "Run code", show=False),
        Binding("f9", "run_code_send", "Run + send", show=False),
        Binding("escape", "stop", "Stop", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, transparent: bool = True, ask_name: bool = False) -> None:
        # ansi_color=True lets Textual emit the terminal's *default* background,
        # so a translucent / blurred terminal shows straight through.
        super().__init__(ansi_color=transparent)
        self.transparent = transparent
        self.ask_name = ask_name
        self.chatting = False
        s = storage.load_settings()
        prov = s.get("provider")
        self.provider_id: str = prov if isinstance(prov, str) and prov in PROVIDERS else "inception"
        model = s.get("model")
        self.model: str | None = client.clean(model)[:200] if isinstance(model, str) and model else None
        # Python access is deliberately NOT persisted: it is OFF at every launch.
        self.python_enabled = False
        self._last_block: str | None = None
        self._run_seq = 0
        bg = s.get("code_bg")
        self.code_bg: str = bg if bg in CODE_BGS else "translucent"
        self._chat_id = 0  # bumped on "new chat" so stale workers can't touch the new conversation
        self.model_cache: dict[str, list[ModelInfo]] = {}
        self.history: list[dict] = []
        name = s.get("username")
        self.username: str = client.clean(name)[:24] if isinstance(name, str) else ""
        theme = s.get("theme")
        self._saved_theme: str | None = theme if isinstance(theme, str) else None

    # ── layout ────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static(id="status")
            with Vertical(id="splash"):
                with Center():
                    yield Logo(id="logo")
                yield Static("A Simple Minimal AI CLI", id="tagline")
            yield VerticalScroll(id="log")
            yield PromptBox(placeholder="Message…   Enter = send · Ctrl+J = new line · Esc = stop", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(ICARUS)
        self.register_theme(GLASS)
        self.register_theme(EMBER)
        wanted = self._saved_theme if self._saved_theme in self.available_themes else "icarus"
        self.theme = wanted
        self.watch(self, "theme", lambda t: storage.save_settings(theme=t), init=False)
        self._apply_code_bg()
        self._update_status()
        self.query_one("#log").display = False
        self.query_one("#prompt", PromptBox).focus()
        if self.ask_name and not self.username:
            self.push_screen(NameScreen(), self._named)
        else:
            self._start()

    def _named(self, name: str | None, start: bool = True) -> None:
        self.username = " ".join(client.clean(name or "").split())[:24]
        storage.save_settings(username=self.username)
        self.query_one("#prompt", PromptBox).focus()
        if start:
            self._start()

    def action_rename(self) -> None:
        self.push_screen(NameScreen(), lambda name: self._named(name, start=False) if name else None)

    def _enter_chat(self) -> None:
        if not self.chatting:
            self.chatting = True
            self.query_one("#splash").display = False
            self.query_one("#log").display = True

    def _start(self) -> None:
        try:
            moved = storage.migrate_legacy(PROVIDERS.values())
            if moved:
                self._notice(f"Moved plaintext keys into the OS keyring: {', '.join(moved)}")
        except Exception:
            pass
        if not storage.backend_info()[1]:
            self._notice("Warning: no OS keyring available - press F3 for details.", "error")
        self._first_run()

    # ── onboarding ────────────────────────────────────────────────────────
    def _providers_with_key(self) -> list[str]:
        return [pid for pid, p in PROVIDERS.items() if storage.get_key(p)]

    def _first_run(self) -> None:
        have = self._providers_with_key()
        if not have:
            urls = "\n".join(f"  {p.name}: {p.key_url}" for p in PROVIDERS.values())
            self._notice(
                "Welcome! To start chatting you need an API key from at least ONE provider "
                f"(you don't need all of them):\n{urls}\n"
                "The key screen is opening now. Later: /keys or F3.  /help lists all commands."
            )
            self.action_keys()
            return
        if not self.model:
            if self.provider_id not in have:
                self.provider_id = have[0]  # e.g. you only have an NVIDIA key
                self._update_status()
            self.action_models()
        else:
            self._notice("/help for commands · F2 models · F3 API keys · F4 theme · F5 new chat")

    def _help(self) -> None:
        self._notice(
            "Typing:  Enter sends · Ctrl+J (or Shift+Enter) adds a new line · pasted text keeps its lines · Esc stops a reply"
        )
        self._notice(
            "Commands:  /keys  add or change API keys   /model  pick provider & model   /theme  change theme\n"
            "           /new  start a new chat          /help   this list                /quit  exit\n"
            "           /python [on|off]  let the model run Python (asks before every run; F8)   /run  run the last code block\n"
            "           /codebg [none|translucent|opaque]  background of code blocks"
        )
        self._notice(
            "Get a key (you only need the providers you use):\n"
            + "\n".join(f"  {p.name}: {p.key_url}" for p in PROVIDERS.values())
        )

    def _command(self, text: str) -> None:
        parts = text.split()
        name, args = parts[0].lower(), [p.lower() for p in parts[1:]]
        simple = {
            "/keys": self.action_keys,
            "/key": self.action_keys,
            "/model": self.action_models,
            "/models": self.action_models,
            "/theme": self.action_change_theme,
            "/name": self.action_rename,
            "/new": self.action_new_chat,
            "/clear": self.action_new_chat,
            "/help": self._help,
            "/?": self._help,
            "/quit": self.exit,
            "/exit": self.exit,
        }
        if name in simple:
            simple[name]()
        elif name in ("/python", "/py"):
            arg = args[0] if args else "toggle"
            states = {"on": True, "off": False, "toggle": None}
            if arg in states:
                self._set_python(states[arg])
            elif arg == "status":
                self._notice(f"Python access is {'ON (asks before every run)' if self.python_enabled else 'off'}.")
            else:
                self._notice("Usage: /python on | off | status", "error")
        elif name == "/run":
            self.action_run_code()
        elif name == "/codebg":
            arg = args[0] if args else CODE_BGS[(CODE_BGS.index(self.code_bg) + 1) % len(CODE_BGS)]
            if arg in CODE_BGS:
                self.code_bg = arg
                storage.save_settings(code_bg=arg)
                self._apply_code_bg()
                self._notice(f"Code block background: {arg}")
            else:
                self._notice("Usage: /codebg none | translucent | opaque", "error")
        else:
            self._notice(f"Unknown command {text[:30]!r}. Type /help.", "error")

    def _apply_code_bg(self) -> None:
        for mode in CODE_BGS:
            self.set_class(mode == self.code_bg, f"-code-{mode}")

    # ── python access ─────────────────────────────────────────────────────
    def action_toggle_python(self) -> None:
        self._set_python(None)

    def _set_python(self, state: bool | None) -> None:
        new = (not self.python_enabled) if state is None else state
        self.python_enabled = new
        if new:
            self._notice(
                "Python access is ON for this session (it turns itself off when you quit).\n"
                "When the model writes a ```python block you get its exact code and three icons: "
                "✓ run (private) · ➤ run + send output to the model · ✕ cancel. Nothing runs until you press one.\n"
                f"Runs use the standard library only, a {pyrunner.TIMEOUT:.0f}s limit, an empty temp folder and no "
                "API keys or environment variables.\n"
                "This is NOT a sandbox: approved code runs as you and can read your files and use the network. "
                "Only approve code you understand."
            )
        else:
            self._clear_run_cards()
            self.workers.cancel_group(self, "run")
            self._notice("Python access is OFF. Nothing will be run.")
        self._update_status()

    def _clear_run_cards(self) -> None:
        for card in list(self.query(RunCard)):
            card.remove()
        self._last_block = None

    def _offer_code(self, text: str) -> None:
        blocks = pyrunner.extract_python_blocks(text)
        if not blocks:
            return
        log = self.query_one("#log", VerticalScroll)
        shown = [c for c in blocks if len(c) <= pyrunner.MAX_CODE_CHARS][:MAX_RUN_CARDS]
        if len(shown) < len(blocks):
            self._notice("Some code blocks were too long or too many to offer for running.")
        for n, code in enumerate(shown, 1):
            self._run_seq += 1
            card = RunCard(code, n, len(shown), id=f"run-{self._run_seq}", classes="runcard")
            log.mount(card)
            self._last_block = code
        log.scroll_end(animate=False)

    @on(RunCard.Decided)
    def _card_decided(self, event: RunCard.Decided) -> None:
        if event.choice == "cancel":
            self._notice("Cancelled - nothing was run.")
            self.query_one("#prompt", PromptBox).focus()
        else:
            self._request_run(event.card.code, event.choice == "send")

    def action_run_code(self) -> None:
        self._request_run(self._last_block, False)

    def action_run_code_send(self) -> None:
        self._request_run(self._last_block, True)

    def _request_run(self, code: str | None, send: bool) -> None:
        if not self.python_enabled:
            return self._notice("Python access is off. Turn it on with /python on (or F8).", "error")
        if code is None:
            return self._notice("There is no code block to run yet.", "error")
        if any(w.group == "run" and not w.is_finished for w in self.workers):
            return self._notice("Something is already running (Esc to stop it).", "error")
        self._execute(code, send, self._chat_id)

    @work(exclusive=True, group="run")
    async def _execute(self, code: str, send: bool, chat_id: int) -> None:
        if not self.python_enabled:  # re-checked at the last moment (it may have been switched off)
            return self._notice("Python access was turned off - nothing was run.", "error")
        prompt = self.query_one("#prompt", PromptBox)
        log = self.query_one("#log", VerticalScroll)
        current = lambda: chat_id == self._chat_id
        handed_to_model = False
        prompt.disabled = True
        self._notice("Running…  (Esc to stop)")
        try:
            secrets = [k for p in PROVIDERS.values() if (k := storage.get_key(p))]
            try:
                res = await pyrunner.run_python(code, secrets=secrets)
            except ValueError as exc:
                return self._notice(f"Not run: {exc}", "error")
            except Exception as exc:
                return self._notice(f"Could not run the code: {type(exc).__name__}", "error")
            if not current():
                return
            body = res.output.rstrip() or "(no output)"
            display = body
            if res.saved_files_dir:
                display += f"\n\nFiles saved to: {res.saved_files_dir}"
            if res.file_save_error:
                display += f"\n\n{res.file_save_error}"
            log.mount(Static(Text(f"▶ python · {res.summary()}\n{display}"), classes="output"))
            log.scroll_end(animate=False)
            if send and self.model:
                self.history.append({"role": "user", "content": f"Python output ({res.summary()}):\n```\n{body}\n```"})
                self._notice("(output sent to the model)")
                handed_to_model = True
                self._generate(self.provider_id, self.model, chat_id)
        finally:
            if current() and not handed_to_model:
                prompt.disabled = False
                prompt.focus()

    def _update_status(self) -> None:
        provider = PROVIDERS[self.provider_id]
        model = self.model or "no model selected (F2)"
        warn = "" if storage.get_key(provider) else "  ·  no API key - /keys"
        py = "python: ON (asks first)" if self.python_enabled else "python: off"
        self.query_one("#status", Static).update(Text(f"{provider.name} · {model}{warn}  ·  {py}"))

    def _notice(self, text: str, css: str = "notice") -> None:
        if css == "error":
            self._enter_chat()
        log = self.query_one("#log", VerticalScroll)
        log.mount(Static(text, classes=css, markup=False))
        log.scroll_end(animate=False)

    # ── actions ───────────────────────────────────────────────────────────
    def action_models(self) -> None:
        def done(result: tuple[str, str] | None) -> None:
            if result:
                self.provider_id, self.model = result
                storage.save_settings(provider=self.provider_id, model=self.model)
                self._update_status()
                self.query_one("#prompt", PromptBox).focus()

        self.push_screen(ModelScreen(self.provider_id, self.model), done)

    def action_keys(self) -> None:
        def done(saved: bool | None) -> None:
            if saved:
                self.model_cache.clear()
                self._notice("API keys saved.")
                have = self._providers_with_key()
                if have and PROVIDERS[self.provider_id].id not in have:
                    self.provider_id, self.model = have[0], None  # switch to a provider you do have
                self._update_status()
                if have and not self.model:
                    return self.action_models()
            self._update_status()
            self.query_one("#prompt", PromptBox).focus()

        self.push_screen(KeysScreen(), done)

    def action_new_chat(self) -> None:
        self._chat_id += 1
        self.workers.cancel_group(self, "chat")
        self.workers.cancel_group(self, "run")
        self._clear_run_cards()
        self.history.clear()
        self.query_one("#log", VerticalScroll).remove_children()
        self.query_one("#prompt", PromptBox).disabled = False

    def action_stop(self) -> None:
        self.workers.cancel_group(self, "chat")
        self.workers.cancel_group(self, "run")

    # ── chat ──────────────────────────────────────────────────────────────
    @on(PromptBox.Submitted, "#prompt")
    def _submit(self, event: PromptBox.Submitted) -> None:
        text = client.clean(event.value).strip()  # drop terminal control chars from pasted text
        if not text:
            return
        self._enter_chat()
        if len(text) > MAX_INPUT_CHARS:
            self._notice(
                f"Message too long ({len(text):,} characters; the limit is {MAX_INPUT_CHARS:,}). "
                "Nothing was sent or cleared.",
                "error",
            )
            return
        if (
            text.startswith("/")
            and "\n" not in text
            and (text.split()[0].lower() in KNOWN_COMMANDS or len(text.split()) == 1)
        ):
            event.box.clear()
            return self._command(text)
        if storage.looks_like_key(text, PROVIDERS.values()):
            # never send a secret to a model or keep it in the chat history / input box
            event.box.clear()
            self._notice("That looks like an API key - it was NOT sent. Paste it into the key screen instead.", "error")
            return self.action_keys()
        provider = PROVIDERS[self.provider_id]
        if not self.model:
            self._notice("Pick a model first (F2).", "error")
            return self.action_models()
        if not storage.get_key(provider):
            self._notice(
                f"No API key for {provider.name}. Add one with /keys, or switch provider with /model.", "error"
            )
            return self.action_keys()
        event.box.clear()
        event.box.disabled = True  # one request at a time
        log = self.query_one("#log", VerticalScroll)
        shown = text.replace("\n", "\n      ")  # indent continuation lines under "you › "
        log.mount(Static(f"{self.username or 'you'} › {shown}", classes="user", markup=False))
        self.history.append({"role": "user", "content": text})
        self._generate(provider.id, self.model, self._chat_id)

    @work(exclusive=True, group="chat")
    async def _generate(self, pid: str, model: str, chat_id: int) -> None:
        provider = PROVIDERS[pid]
        log = self.query_one("#log", VerticalScroll)
        prompt = self.query_one("#prompt", PromptBox)
        # open_links=False: model output must never be able to launch a browser/URL handler
        reply = Markdown("", classes="assistant", open_links=False)
        await log.mount(reply)
        parts: list[str] = []
        key = storage.get_key(provider) or ""
        current = lambda: chat_id == self._chat_id  # False once "new chat" was pressed
        ok = False
        messages = self.history[-MAX_HISTORY:]
        system = []
        if self.username:
            system.append(f"The user's name is {self.username}. Address them by this name when it feels natural.")
        if self.python_enabled:  # only tell the model it can run code while the toggle is on
            system.append(pyrunner.SYSTEM_PROMPT)
        if system:
            messages = [{"role": "system", "content": "\n\n".join(system)}] + messages
        try:
            async for chunk in client.stream_chat(provider, key, model, messages, MAX_TOKENS):
                parts.append(chunk)
                await reply.append(chunk)
                log.scroll_end(animate=False)
            ok = True
        except client.APIError as exc:
            if current():
                self._notice(f"Error: {exc}", "error")
        except Exception as exc:
            if current():
                self._notice(f"Request failed: {client.redact(str(exc) or type(exc).__name__, key)}", "error")
        finally:
            key = ""  # don't leave the secret in a frame that a traceback could print
            if current():
                text = "".join(parts)
                if text:
                    self.history.append({"role": "assistant", "content": text})
                    if ok and self.python_enabled:  # only a complete reply is offered for running
                        self._offer_code(text)
                elif self.history and self.history[-1]["role"] == "user":
                    self.history.pop()  # nothing came back; drop the dangling prompt
                    try:
                        await reply.remove()
                    except Exception:
                        pass
                prompt.disabled = False
                prompt.focus()

    def _fatal_error(self) -> None:
        """Textual prints crash tracebacks WITH local variables (show_locals=True),
        which could dump API keys to the terminal/scrollback. Render without locals."""
        import rich
        from rich.traceback import Traceback
        from textual.app import Segments

        self.bell()
        tb = Traceback(show_locals=False, width=None, suppress=[rich])
        self._exit_renderables.append(Segments(self.console.render(tb, self.console.options)))
        self._close_messages_no_wait()


def main() -> None:
    parser = argparse.ArgumentParser(prog="icarus-cli", description=__doc__)
    parser.add_argument(
        "--opaque", action="store_true", help="use the theme's solid background instead of transparency"
    )
    parser.add_argument("--version", action="version", version=f"icarus-cli {__version__}")
    args = parser.parse_args()
    ChatApp(transparent=not args.opaque, ask_name=True).run()


if __name__ == "__main__":
    main()
