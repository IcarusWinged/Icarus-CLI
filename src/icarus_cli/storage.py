"""Key + settings storage.

API keys are stored in the operating system's keyring (macOS Keychain, Windows
Credential Locker, Secret Service/KWallet on Linux). Only if no keyring exists
do we fall back to a mode-600 keys.json (shared with the `llm` CLI) and warn.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from .providers import Provider


def user_dir() -> Path:
    override = os.environ.get("LLM_USER_PATH")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "io.datasette.llm"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "io.datasette.llm"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        path = base / "io.datasette.llm"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)  # only affects a newly created dir
    return path


def _read(path: Path, strict: bool = False) -> dict:
    """Read a JSON object. strict=True raises on a corrupt file instead of
    returning {} (so we never overwrite and destroy keys we couldn't parse)."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError:
        if strict:
            raise
        return {}
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data
    except ValueError as exc:
        if strict:
            raise ValueError(f"{path} is not valid JSON ({exc}); fix or remove it first") from exc
        return {}


def _write(path: Path, data: dict) -> None:
    """Atomic write; the file is created 0600 by mkstemp, never world-readable."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_KEY_RE = re.compile(r"^[\x21-\x7e]{1,512}$")  # printable ASCII, no whitespace/control chars


def validate_key(key: str) -> str:
    key = key.strip()
    if not _KEY_RE.match(key):
        raise ValueError("API key must be 1-512 printable ASCII characters with no spaces")
    return key


SERVICE = "icarus-cli"
_cache: dict[str, str] = {}  # per-session, so we don't hit the OS keychain on every call


def _reset_cache() -> None:
    _cache.clear()


def _keyring():
    """Return the `keyring` module if a real secure backend exists, else None."""
    try:
        import keyring
        from keyring.backends import fail, null

        if isinstance(keyring.get_keyring(), (fail.Keyring, null.Keyring)):
            return None
        return keyring
    except Exception:
        return None


def backend_info() -> tuple[str, bool]:
    """(human description, is_secure) of where new keys will be stored."""
    kr = _keyring()
    if kr is None:
        return (
            f"NO OS KEYRING FOUND - keys fall back to a plaintext file ({user_dir() / 'keys.json'}, mode 600)",
            False,
        )
    be = kr.get_keyring()
    ident = f"{type(be).__module__}.{type(be).__name__}"
    for needle, label in (
        ("macOS", "macOS Keychain"),
        ("Windows", "Windows Credential Locker"),
        ("SecretService", "Secret Service (GNOME Keyring / KeePassXC)"),
        ("kwallet", "KWallet"),
    ):
        if needle in ident:
            return (f"Stored in the OS keyring: {label}", True)
    return (f"Stored in the OS keyring: {type(be).__name__}", True)


def _file_key(provider: Provider) -> str | None:
    v = _read(user_dir() / "keys.json").get(provider.key_alias)
    return v if isinstance(v, str) and v else None


def _lookup(provider: Provider) -> tuple[str | None, str | None]:
    """(key, source) with source in {'keyring','file','env'}."""
    kr = _keyring()
    if kr:
        try:
            v = kr.get_password(SERVICE, provider.key_alias)
            if isinstance(v, str) and v:
                return v, "keyring"
        except Exception:
            pass
    v = _file_key(provider)
    if v:
        return v, "file"
    v = os.environ.get(provider.env_var)
    return (v, "env") if v else (None, None)


def get_key(provider: Provider) -> str | None:
    if provider.id in _cache:
        return _cache[provider.id]
    key, _ = _lookup(provider)
    try:
        key = validate_key(key) if key else None
    except ValueError:
        return None
    if key:
        _cache[provider.id] = key
    return key


def key_source(provider: Provider) -> str | None:
    return _lookup(provider)[1] if get_key(provider) else None


def set_key(provider: Provider, key: str) -> None:
    """Save (or, with an empty string, delete) a key. Prefers the OS keyring;
    if a keyring exists but errors we raise rather than silently downgrade to a file."""
    key = key.strip()
    if key:
        validate_key(key)
    _cache.pop(provider.id, None)
    kr = _keyring()
    path = user_dir() / "keys.json"
    if kr:
        try:
            if key:
                kr.set_password(SERVICE, provider.key_alias, key)
            else:
                try:
                    kr.delete_password(SERVICE, provider.key_alias)
                except Exception:
                    pass  # already absent
        except Exception as exc:
            raise OSError(f"OS keyring error: {exc}") from None
        # scrub any legacy plaintext copy now that the secret is safely stored
        try:
            data = _read(path, strict=True)
            if provider.key_alias in data:
                data.pop(provider.key_alias)
                _write(path, data)
        except (ValueError, OSError):
            pass
        return
    data = _read(path, strict=True)  # preserve keys for other providers/plugins
    if key:
        data[provider.key_alias] = key
    else:
        data.pop(provider.key_alias, None)
    _write(path, data)


def migrate_legacy(providers) -> list[str]:
    """Move plaintext keys (ours or `llm` CLI's aliases) from keys.json into the
    OS keyring. Only removes a plaintext copy after reading the keyring copy back."""
    kr = _keyring()
    if not kr:
        return []
    path = user_dir() / "keys.json"
    try:
        data = _read(path, strict=True)
    except (ValueError, OSError):
        return []
    moved: list[str] = []
    for p in providers:
        v = data.get(p.key_alias)
        if not (isinstance(v, str) and v):
            continue
        try:
            validate_key(v)
            kr.set_password(SERVICE, p.key_alias, v)
            if kr.get_password(SERVICE, p.key_alias) != v:
                continue
        except Exception:
            continue
        data.pop(p.key_alias)
        moved.append(p.name)
    if moved:
        _write(path, data)
        _cache.clear()
    return moved


def looks_like_key(text: str, providers) -> bool:
    """True if `text` is (almost certainly) an API key rather than a chat message:
    a single token that equals a stored key or starts with a known key prefix."""
    t = text.strip()
    if len(t) < 16 or not _KEY_RE.match(t):
        return False
    if any(t == get_key(p) for p in providers):
        return True
    return t.startswith(tuple(pre for p in providers for pre in p.key_prefixes))


def mask(key: str | None) -> str:
    if not key:
        return "not set"
    return "•" * 8 + key[-4:] if len(key) > 8 else "•" * len(key)


SETTINGS_FILE = "icarus-cli.json"


def load_settings() -> dict:
    return _read(user_dir() / SETTINGS_FILE)


def save_settings(**updates) -> None:
    path = user_dir() / SETTINGS_FILE
    data = _read(path)
    data.update(updates)
    _write(path, data)
