<p align="center">
  <img src="src/icarus_cli/logo.svg" alt="Icarus CLI" width="480">
</p>

<p align="center">A simple, minimal AI chat for your terminal.</p>

# Icarus CLI

Early build (alpha, version 0.1.3). Expect bugs and missing features.

<p align="center">
  <img src="docs/splash.png" alt="Icarus CLI main screen" width="720">
</p>

## What it can do

- Chat with Gemini (Google AI), Inception Labs, NVIDIA and OpenRouter models from one terminal app
- Browse the live model list for your key, split into free and paid where the provider reports it
- Stream replies as they are generated, with a multi-line input box
- Switch themes, and set a name the model uses to address you
- Optionally let the model run Python code, off by default and only after you approve each run

## Install

```bash
pip install icarus_cli-0.1.3-py3-none-any.whl
icarus-cli
```

On first run, add an API key for any one provider. Press F2 to pick a model, F3 to manage keys, and type /help for commands.

## Warning: keyring on Linux

API keys are stored in your operating system keyring. Some Linux setups (minimal installs, servers, containers, no desktop session) have no working keyring, and saving a key can fail. When no keyring exists, Icarus CLI warns you and falls back to a plain file with restricted permissions. You can also skip storage entirely and set an environment variable such as GEMINI_API_KEY.

## Roadmap

- [ ] Chat history
- [ ] Chat memory
- [ ] More safety guardrails
