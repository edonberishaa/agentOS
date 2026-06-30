"""__main__.py — entry point for `python -m agentos_gateway`.

Loads .env from the current working directory before startup so
developers can put ANTHROPIC_API_KEY (and other config) in a
packages/gateway/.env file without exporting to the shell.
"""

from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can also be set in the shell

from .main import main

if __name__ == "__main__":
    main()
