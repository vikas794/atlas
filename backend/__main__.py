"""Run Atlas: ``uv run python -m backend`` (add ``--reload`` for development).

Host and port come from src/configs/config.yaml (``host``, ``port``; default 127.0.0.1:8000).
The API, the background worker and, when ``frontend/dist`` exists, the built UI are served
by this one process on one port. Ctrl+C (SIGINT) or SIGTERM shuts down gracefully: the
lifespan stops the worker before the process exits.

An invalid configuration prints the list of problems and exits with status 2 instead of a
traceback.
"""

from __future__ import annotations

import argparse
import socket
import sys
from dataclasses import replace


def port_problem(host: str, port: int) -> str | None:
    """A readable reason the server cannot listen on ``host:port``, or None when it can.

    Checked up front so "address already in use" (another Atlas, the Docker container, a
    stale dev server) is one clear line instead of a uvicorn traceback, and so a dev proxy
    never silently talks to the other server.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return f"Cannot resolve host {host!r} from config.yaml: {exc}"
    family, _, _, _, address = infos[0]
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if sys.platform == "win32":
            # Without this, Windows lets a second socket bind a port that is in use.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(address)
        except OSError:
            return (
                f"Port {port} on {host} is already in use (another Atlas, its Docker "
                "container or an old dev server?). Stop it, or set another `port` in "
                "src/configs/config.yaml (or ATLAS_PORT in .env)."
            )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reload", action="store_true", help="restart on source changes (development only)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only validate the configuration and that the port is free, then exit",
    )
    args = parser.parse_args(argv)

    from src.config.settings import SettingsError, load_settings, repo_root

    try:
        settings = load_settings()
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2

    busy = port_problem(settings.host, settings.port)
    if busy:
        print(busy, file=sys.stderr)
        return 1
    if args.check:
        print(f"Configuration OK; http://{settings.host}:{settings.port} is free.")
        return 0

    import uvicorn

    from src.shared.logging_setup import configure_logging

    # With --reload this process only supervises; the server child configures its own logging
    # (backend.main). Two processes rotating one log file fails on Windows, so the supervisor
    # logs to the console only.
    configure_logging(replace(settings, log_file_logging=False) if args.reload else settings)
    root = repo_root()
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        reload_dirs=[str(root / "backend"), str(root / "src")] if args.reload else None,
        # Atlas' own logging (redacted, also to logs/atlas.log) handles uvicorn's loggers
        # too; the request middleware replaces the access log, which the UI's polling floods.
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
