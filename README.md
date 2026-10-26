<p align="center">
  <img src="src/icarus_cli/logo.svg" alt="Icarus CLI" width="480">
</p>

<p align="center">A simple, minimal AI chat for your terminal.</p>

# Icarus CLI

Version 0.1.3

Note: Icarus CLI is in alpha. The code can be buggy. On Linux, the system keyring may fail or be missing; the app then warns you and falls back to a plain file with restricted permissions (or you can use environment variables).

<p align="center">
  <img src="docs/splash.png" alt="Icarus CLI main screen" width="720">
</p>

## Providers

Inception Labs, NVIDIA, Google AI (Gemini) and OpenRouter. You only need a key for one of them.

## Install and run

```bash
pip install icarus_cli-0.1.3-py3-none-any.whl
icarus-cli            # transparent background
icarus-cli --opaque   # solid background
```

To run from source:

```bash
pip install -e ".[test]"
icarus-cli
```

## API keys

On first run the key screen opens by itself. Create a key for your provider, paste it, and press Save.

- Inception Labs: platform.inceptionlabs.ai/dashboard/api-keys
- NVIDIA: build.nvidia.com/settings/api-keys
- Google AI: aistudio.google.com/apikey
- OpenRouter: openrouter.ai/keys

Keys are stored in your OS keyring. These environment variables also work: INCEPTION_API_KEY, NVIDIA_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY. Never paste a key into the chat box; it is not sent to the model.

## Commands

/keys  /model  /name  /theme  /new  /python  /run  /codebg  /help  /quit

## Shortcuts

| Key | Action |
|-----|--------|
| Enter | Send |
| Ctrl+J | New line |
| F2 | Provider and model menu |
| F3 | API key manager |
| F4 | Theme picker |
| F5 | New chat |
| F8 | Python on/off |
| Esc | Stop generating |
| Ctrl+Q | Quit |

## Python execution (off by default)

Turn it on with /python on or F8. It is never saved and is off at every launch. When the model writes a python block, you get the exact code and three buttons. Nothing runs until you press one.

| Button | Key | Action |
|--------|-----|--------|
| Run | Ctrl+R | Run and keep the output private |
| Send | F9 | Run and send the output to the model |
| Cancel | Esc | Do not run |

Code runs in a throw-away subprocess with no API keys in its environment, a timeout and output limits. This is containment, not a sandbox: approved code still runs as you, so only approve code you understand.

## Development

```bash
pip install -e ".[test]"
pytest
python -m build --wheel
```

## Roadmap

- [ ] Chat history
- [ ] Chat memory
- [ ] More safety guardrails
