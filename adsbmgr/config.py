"""Generic connection defaults — nothing site-specific lives here."""

from pathlib import Path

SSH_PORT = 22
SSH_USER = "pi"
SSH_TIMEOUT = 10

SSH_KEY_PATHS = [
    Path.home() / ".ssh" / "id_ed25519",
    Path.home() / ".ssh" / "id_rsa",
    Path.home() / ".ssh" / "id_ecdsa",
]
