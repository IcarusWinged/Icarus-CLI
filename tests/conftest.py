import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import PasswordDeleteError

from icarus_cli import storage


class MemKeyring(KeyringBackend):
    """In-memory stand-in for macOS Keychain / Credential Locker / Secret Service."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.d = {}

    def set_password(self, service, user, password):
        self.d[(service, user)] = password

    def get_password(self, service, user):
        return self.d.get((service, user))

    def delete_password(self, service, user):
        try:
            del self.d[(service, user)]
        except KeyError:
            raise PasswordDeleteError("not found")


class BrokenKeyring(MemKeyring):
    def set_password(self, service, user, password):
        raise RuntimeError("keychain is locked")


@pytest.fixture(autouse=True)
def mem_keyring():
    old, kr = keyring.get_keyring(), MemKeyring()
    keyring.set_keyring(kr)
    storage._reset_cache()
    yield kr
    keyring.set_keyring(old)
    storage._reset_cache()


@pytest.fixture
def no_keyring():
    keyring.set_keyring(fail.Keyring())
    storage._reset_cache()
