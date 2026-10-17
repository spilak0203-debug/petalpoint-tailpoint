"""Tiny Photocraft control client for a headless `photocraft-cli serve --port <port>` session.

Same usage as pc.py, with its own defaults so it never talks to the live app by accident:
Port:       --port, else $PHOTOCRAFT_SERVE_PORT, else 7879.
Token file: --token-file, else $PHOTOCRAFT_SERVE_TOKEN_FILE, else ~/.photocraft/control-<port>.token.
"""
import pc
from pc import send, short, take_args  # noqa: F401  (re-exported for the draw scripts)

CONFIG = pc.settings("PHOTOCRAFT_SERVE_PORT", "PHOTOCRAFT_SERVE_TOKEN_FILE", 7879)


def connect():
    return pc.connect(CONFIG)


if __name__ == "__main__":
    pc.main(CONFIG)
