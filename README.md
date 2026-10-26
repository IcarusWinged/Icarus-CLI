<p align="center">
  <img src="src/icarus_cli/logo.svg" alt="Icarus CLI" width="480">
</p>

<p align="center">A simple, minimal AI chat for your terminal.</p>

# Icarus CLI

Version 0.1.3

A clean, transparent [Textual](https://textual.textualize.io) chat TUI for four providers. Each has its own connector in `src/icarus_cli/connectors/` (`inception.py`, `nvidia.py`, `google.py`, `openrouter.py`):

| Provider       | Endpoint                                   | Models                          |
|----------------|--------------------------------------------|---------------------------------|
| Inception Labs | `https://api.inceptionlabs.ai/v1`          | everything `/models` returns    |
| NVIDIA         | `https://integrate.api.nvidia.com/v1`      | everything your key can see     |
| Google AI      | official `google-genai` SDK (`pip install google-genai`) | every text model with `generateContent`, listed as Free and Paid |
| OpenRouter     | `https://openrouter.ai/api/v1`             | every text model, listed as Free (price 0) and Paid |

## Install & run

```bash
pip install icarus_cli-0.1.3-py3-none-any.whl
icarus-cli            # transparent background (default)
icarus-cli --opaque   # use the theme's solid background
```

## Adding your API key (first run)

You only need a key for one provider - you don't need Gemini (or any other) if you don't use it.

1. Run `icarus-cli`. With no keys saved, the key screen opens by itself.
2. Create a key at the address shown under each provider and paste it in:
   - Inception Labs: platform.inceptionlabs.ai/dashboard/api-keys
   - NVIDIA: build.nvidia.com/settings/api-keys
   - Google AI: aistudio.google.com/apikey
   - OpenRouter: openrouter.ai/keys
3. Press Save. The model menu opens next, showing only providers you have a key for as usable
   (others are marked `(no key)`; selecting one asks for its key right there).

On first launch a name screen appears (the model then addresses you by that name; change it any time with `/name`), then the ICARUS logo screen stays until your first message, then the chat opens. The logo is `logo.svg`, drawn at whatever width fits your terminal, and the whole UI sits in a centred column.

Later, type `/keys` in the chat box (or press F3) to add, replace or clear a key.
Keys are stored in your OS keyring (macOS Keychain / Windows Credential Locker / Secret Service or KWallet).
If no keyring exists, `icarus-cli` warns you and falls back to a mode-600 file. Env vars
`INCEPTION_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` also work.
Never paste a key into the chat box: if you do, it is not sent to the model and the key screen opens instead.

## Typing messages

The input box is multi-line: it grows as you type (up to 12 rows) and then scrolls.

| Key | Action |
|-----|--------|
| Enter | Send |
| Ctrl+J (also Shift+Enter / Alt+Enter where your terminal supports them) | New line |
| Paste | Keeps every line |

Messages are limited to 100,000 characters (nothing is cleared if you go over).

## Letting the model run Python (off by default)

Toggle with `/python on` / `/python off` or F8. The status bar always shows `python: off` or `python: ON (asks first)`.
It is never saved: every launch starts with Python access off.

When it is on, the model is told it can run code. If its reply has a complete ```` ```python ```` block, the exact code
appears in the chat with three buttons: run (private), run and send output to the model, and cancel. Nothing runs until you press one:

| Button | Key | Action |
|------|-----|--------|
| Run | Ctrl+R (or `/run`) | Run, keep output private |
| Send | F9 | Run, send output to the model |
| Cancel | Esc / click | Cancel |

What a run looks like: a throw-away `python -I` subprocess (packages installed in icarus-cli's environment are importable;
`PYTHONPATH` and user site-packages are ignored),
an empty temp folder as its working directory (deleted afterwards), no environment variables or API keys, stdin closed,
20 s timeout (10 min while a window is open), output cap, whole process-group kill, and (Linux/macOS) memory / CPU / file-size limits. Any stored API key
(and its reverse, base64 and hex forms) is redacted from the output. Esc stops a run.
Files created by a run are copied into a unique folder under the user's app-data directory (`%LOCALAPPDATA%\icarus-cli\files`
on Windows, `~/Library/Application Support/icarus-cli/files` on macOS, or `$XDG_DATA_HOME/icarus-cli/files` on Linux;
defaults to `~/.local/share/icarus-cli/files`). The run output displays the exact folder path.

Code that calls `plt.show()` (or opens tkinter / turtle / pygame windows) gets a real, interactive window on your screen.
matplotlib uses the PySide6 Qt backend, so you get the full toolbar (zoom, pan, save). The run lasts until you close the window
(up to 10 minutes) and Esc in the chat stops it at any time. Code that never calls `show()` stays windowless. Library caches
(e.g. matplotlib's font json) live outside the working folder, so only files your code creates are saved.

This is containment, not a sandbox. Approved code still runs as *you*: it can read your files and use the network, and a
determined snippet can get around the redaction (e.g. by encoding a secret some other way). The approval icons are the real
safeguard, so only approve code you understand. Windows: the process-tree kill works, but memory/CPU limits are not applied.

## Code blocks

`/codebg none|translucent|opaque` (no argument cycles). `translucent` is a subtle theme-aware tint, `opaque` a solid panel
colour, `none` the terminal's own background. Most terminals apply their transparency only to the *default* background, so a
tinted block is drawn as a solid colour there. The choice is remembered.

## Chat commands

`/keys` `/model` `/name` `/theme` `/new` `/python` `/run` `/codebg` `/help` `/quit` (messages starting with `/` are never sent to a model)

## Shortcuts

| Key | Action |
|-----|--------|
| F2 / Ctrl+O | Provider + model menu (live list from your key, type to filter) |
| F3 / Ctrl+G | API key manager |
| F4 / Ctrl+T | Theme picker (all built-in Textual themes + `glass`, `ember`) |
| F5 / Ctrl+N | New chat |
| Esc | Stop generating |
| Ctrl+Q | Quit |

The chat is deliberately restricted: text only, streaming replies, last 20
messages sent as context, 2048 max tokens, no tools/attachments/system prompts.
Theme, provider and model are remembered in `icarus-cli.json` next to `keys.json`.

## Security

Tested against a hostile/buggy API server and hostile input (`pytest tests/test_security.py`):

- Key file is created `0600` atomically (never briefly world-readable); new config dir is `0700`.
- A corrupt `keys.json` is never overwritten (other keys are not destroyed); keys are validated
  (printable ASCII, no whitespace) so a pasted value can't inject HTTP headers.
- API keys are redacted from every error message; keys are only ever sent over HTTPS, redirects are not followed, TLS is verified.
- Crash tracebacks are rendered without local variables (Textual's default would print them, including keys).
- Model output is stripped of terminal control characters / escape sequences / bidi overrides, links in replies are not clickable, and no markup from the server is interpreted.
- Bounded reads: reply size, line size, response size and pagination are all capped; malformed JSON/SSE is ignored or reported cleanly.

Known limitation: like the `llm` CLI, keys are stored in plaintext in `keys.json` (protected only by file permissions).
Anyone who can read your files as your user can read them. Use environment variables if you prefer not to store keys on disk.

## Development

```bash
pip install -e ".[test]"
pytest                      # ~100 tests incl. adversarial/security tests (takes a few minutes)
pip install build && python -m build --wheel
```

Layout: `src/icarus_cli/` (`app.py` UI, `screens.py` dialogs, `widgets.py` input box + run icons, `client.py` HTTP/SSE,
`storage.py` keys + settings, `pyrunner.py` Python runner, `providers.py`), `tests/`.

## Free and paid models

The model menu (F2) splits a provider's models into Free and Paid sections when the provider says which is which.
OpenRouter reports prices, so free means both prompt and completion cost 0. The Gemini API does not report it, so
`FREE_TIER` in `connectors/google.py` mirrors Google's pricing page (edit it when that page changes); unmatched Gemini models
are shown as Paid. Inception and NVIDIA publish no free/paid flag and are shown as one list.

## Adding a provider

Add a `Provider` in `providers.py`, create `connectors/<name>.py` with a `Connector` subclass (`list_models` and
`stream_chat`), and register it in `connectors/__init__.py`. OpenAI-compatible APIs only need to call
`client.list_openai_models` / `client.stream_openai`.

## Roadmap

Planned for upcoming releases:

- [ ] Chat history
- [ ] Chat memory
- [ ] More safety guardrails
