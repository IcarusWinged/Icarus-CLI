"""Chat input box and the inline run-approval card."""

from __future__ import annotations

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Static, TextArea

from . import pyrunner


class PromptBox(TextArea):
    """Enter sends. Ctrl+J / Shift+Enter / Alt+Enter insert a newline.

    Pasted text keeps all its lines. The box grows with its content up to
    `max-height` (see app.tcss) and scrolls beyond that.
    """

    NEWLINE_KEYS = ("ctrl+j", "shift+enter", "alt+enter")

    class Submitted(Message):
        def __init__(self, box: "PromptBox", value: str) -> None:
            super().__init__()
            self.box = box
            self.value = value

        @property
        def control(self) -> "PromptBox":
            return self.box

    def __init__(self, placeholder: str = "", **kwargs) -> None:
        super().__init__(
            "",
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            placeholder=placeholder,
            **kwargs,
        )

    def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self, self.text))
        elif event.key in self.NEWLINE_KEYS:
            event.prevent_default()
            event.stop()
            self.insert("\n")


class RunCard(Vertical):
    """The exact code, plus tiny icons to approve it: ✓ run · ➤ run + send · ✕ cancel."""

    ICONS = (
        ("✓", "private", "Run, keep output private  (Ctrl+R)"),
        ("➤", "send", "Run, send output to model  (F9)"),
        ("✕", "cancel", "Cancel  (Esc)"),
    )

    class Decided(Message):
        def __init__(self, card: "RunCard", choice: str) -> None:
            super().__init__()
            self.card = card
            self.choice = choice

    def __init__(self, code: str, index: int, total: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.code = code
        self.index = index
        self.total = total

    def compose(self) -> ComposeResult:
        lines = self.code.count("\n") + 1
        yield Static(Text(self.code), classes="codetext")
        with Horizontal(classes="runbar"):
            for glyph, choice, tip in self.ICONS:
                yield Button(glyph, id=f"icon-{choice}", classes=f"icon {choice}", compact=True, tooltip=tip)
            yield Static(
                f"block {self.index}/{self.total} · {lines} lines · {pyrunner.TIMEOUT:.0f}s · runs as you",
                classes="runinfo",
                markup=False,
            )

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Decided(self, (event.button.id or "").removeprefix("icon-")))
