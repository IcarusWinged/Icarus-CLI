"""Modal screens: API key manager and provider/model selection menu."""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Label, OptionList
from textual.widgets.option_list import Option

from . import client, storage
from .connectors import ModelInfo
from .logo import Logo
from .providers import PROVIDERS


class NameScreen(Screen[str]):
    """First screen: ask for a username, then hand over to the chat."""

    def compose(self) -> ComposeResult:
        with Vertical(id="name-stage"):
            with Center():
                yield Logo(id="name-logo")
            with Center():
                yield Label("ENTER YOUR NAME", id="name-label")
            with Center():
                with Horizontal(id="namebox"):
                    yield Input(placeholder="username...", max_length=24, id="name-input", compact=True)
                    yield Button("SAVE", id="name-save", compact=True)

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    @on(Input.Submitted, "#name-input")
    @on(Button.Pressed, "#name-save")
    def _commit(self) -> None:
        field = self.query_one("#name-input", Input)
        if name := field.value.strip():
            self.dismiss(name)
        else:
            field.focus()


class KeysScreen(ModalScreen[bool]):
    """Store / replace / clear API keys (OS keyring)."""

    BINDINGS = [("escape", "cancel", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("API keys", classes="title")
            yield Label(
                "One key is enough - leave the rest blank. Create a key at the address under each provider.",
                classes="hint",
                markup=False,
            )
            desc, secure = storage.backend_info()
            yield Label(desc, classes="hint" if secure else "warn", markup=False)
            for p in PROVIDERS.values():
                yield Label(f"{p.name}  ·  {p.key_url}", classes="field-label tight", markup=False)
                with Horizontal(classes="row"):
                    yield Input(
                        password=True,
                        compact=True,
                        id=f"key-{p.id}",
                        placeholder="paste key here (blank = keep current)",
                    )
                    yield Button("Clear", id=f"clear-{p.id}", classes="small", compact=True)
                yield Label("", id=f"status-{p.id}", classes="hint")
            yield Label("", id="error", classes="warn")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save", compact=True)
                yield Button("Cancel", id="cancel", compact=True)

    def on_mount(self) -> None:
        for p in PROVIDERS.values():
            self._refresh(p.id)
        self.query_one(f"#key-{next(iter(PROVIDERS))}", Input).focus()

    def _refresh(self, pid: str) -> None:
        p = PROVIDERS[pid]
        key = storage.get_key(p)
        src = storage.key_source(p)
        source = {"keyring": "in keyring", "file": "plaintext file", "env": f"from ${p.env_var}"}.get(src, "")
        text = f"{storage.mask(key)}  ({source})" if key else "no key set"
        self.query_one(f"#status-{pid}", Label).update(Text(text))

    def _error(self, msg: str) -> None:
        self.query_one("#error", Label).update(Text(msg))

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save":
            try:
                entries = [(p, self.query_one(f"#key-{p.id}", Input).value.strip()) for p in PROVIDERS.values()]
                for p, value in entries:  # validate everything before writing anything
                    if value:
                        storage.validate_key(value)
                for p, value in entries:
                    if value:
                        storage.set_key(p, value)
            except (ValueError, OSError) as exc:
                self._error(f"Not saved: {exc}")
                return
            self.dismiss(True)
        elif bid == "cancel":
            self.dismiss(False)
        elif bid.startswith("clear-"):
            pid = bid.removeprefix("clear-")
            try:
                storage.set_key(PROVIDERS[pid], "")
            except (ValueError, OSError) as exc:
                self._error(f"Could not clear: {exc}")
                return
            self.query_one(f"#key-{pid}", Input).value = ""
            self._error("")
            self._refresh(pid)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ModelScreen(ModalScreen["tuple[str, str] | None"]):
    """Pick a provider, then one of the models your key can access."""

    BINDINGS = [("escape", "cancel", "Close"), ("f3", "keys", "API keys")]

    def __init__(self, provider_id: str, model: str | None) -> None:
        super().__init__()
        self.provider_id = provider_id
        self.current_model = model
        self._models: list[ModelInfo] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Select model", classes="title")
            with Horizontal(id="panes"):
                with Vertical(id="provider-pane"):
                    yield Label("Provider", classes="field-label")
                    yield OptionList(*self._provider_options(), id="providers")
                with Vertical(id="model-pane"):
                    yield Label("Model", classes="field-label")
                    yield Input(placeholder="type to filter…", id="filter")
                    yield OptionList(id="models")
            yield Label("", id="msg", classes="hint")
            yield Label("F3 = add / change API keys", classes="hint")

    def _provider_options(self) -> list[Option]:
        out = []
        for p in PROVIDERS.values():
            has = bool(storage.get_key(p))
            label = Text(p.name) if has else Text(f"{p.name}  (no key)", style="dim")
            out.append(Option(label, id=p.id))
        return out

    def on_mount(self) -> None:
        providers = self.query_one("#providers", OptionList)
        providers.highlighted = list(PROVIDERS).index(self.provider_id)
        providers.focus()

    @on(OptionList.OptionHighlighted, "#providers")
    def _provider_changed(self, event: OptionList.OptionHighlighted) -> None:
        self.provider_id = event.option_id or self.provider_id
        self._load(self.provider_id)

    @on(OptionList.OptionSelected, "#providers")
    def _provider_chosen(self) -> None:
        if storage.get_key(PROVIDERS[self.provider_id]):
            self.query_one("#filter", Input).focus()
        else:
            self.action_keys()  # picking a provider you have no key for -> ask for it right here

    def action_keys(self) -> None:
        def done(saved: bool | None) -> None:
            if saved:
                self.app.model_cache.clear()  # type: ignore[attr-defined]
                providers = self.query_one("#providers", OptionList)
                keep = providers.highlighted
                providers.clear_options()
                providers.add_options(self._provider_options())
                providers.highlighted = keep
                self._load(self.provider_id)

        self.app.push_screen(KeysScreen(), done)

    @work(exclusive=True)
    async def _load(self, pid: str) -> None:
        provider = PROVIDERS[pid]
        msg = self.query_one("#msg", Label)
        self._models = []
        self._render_models()
        key = storage.get_key(provider)
        if not key:
            msg.update(
                Text(
                    f"No API key for {provider.name}. Press Enter or F3 to add one "
                    f"({provider.key_url}) - or pick a provider you do have."
                )
            )
            return
        cache = self.app.model_cache  # type: ignore[attr-defined]
        if pid not in cache:
            msg.update(Text("Loading models…"))
            try:
                cache[pid] = await client.list_models(provider, key)
            except Exception as exc:  # network / auth errors
                msg.update(Text(f"Could not load models: {client.redact(str(exc), key)}"))
                return
        self._models = cache[pid]
        msg.update(Text(f"{len(self._models)} models available"))
        self._render_models()

    def _render_models(self) -> None:
        needle = self.query_one("#filter", Input).value.lower().strip()
        olist = self.query_one("#models", OptionList)
        olist.clear_options()
        shown = [m for m in self._models if needle in m.id.lower() or needle in m.name.lower()]
        groups = [("Free", [m for m in shown if m.free is True]), ("Paid", [m for m in shown if m.free is False])]
        if any(m.free is not None for m in shown):  # the provider says which are free: show them separately
            groups.append(("Not specified", [m for m in shown if m.free is None]))
        else:
            groups = [("", shown)]
        entries: list[Option] = []
        for title, models in groups:
            if not models:
                continue
            if title:
                entries.append(Option(Text(f"── {title} ({len(models)}) ──", style="bold"), disabled=True))
            for m in models:
                label = Text(m.id)
                if m.name and m.name != m.id:
                    label.append(f"   {m.name}", style="dim")
                entries.append(Option(label, id=m.id))  # Text => no markup parsing
        olist.add_options(entries)
        current = self.current_model if self.provider_id == self.app.provider_id else None  # type: ignore[attr-defined]
        ids = [e.id for e in entries]
        if current in ids:
            olist.highlighted = ids.index(current)
        else:
            olist.highlighted = next((n for n, e in enumerate(entries) if e.id is not None), None)

    @on(Input.Changed, "#filter")
    def _filter(self) -> None:
        self._render_models()

    @on(Input.Submitted, "#filter")
    def _filter_submit(self) -> None:
        olist = self.query_one("#models", OptionList)
        if olist.highlighted is not None:
            olist.action_select()

    def on_key(self, event) -> None:
        if event.key == "down" and isinstance(self.focused, Input):
            self.query_one("#models", OptionList).focus()
            event.stop()

    @on(OptionList.OptionSelected, "#models")
    def _model_chosen(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.dismiss((self.provider_id, event.option_id))

    def action_cancel(self) -> None:
        self.dismiss(None)
