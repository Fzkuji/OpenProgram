"""Run the Server in the foreground with ``python -m openprogram.interfaces.server``."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="python -m openprogram.interfaces.server",
        description="Start the OpenProgram Server in the foreground.",
    )
    parser.add_argument(
        "--port", "-p", type=int, default=None,
        help="Port to serve on (default: the resolved worker port, 18100)",
    )
    parser.add_argument(
        "--browser", action="store_true",
        help="Open a browser window at the server port after start",
    )
    args = parser.parse_args()

    from openprogram.host.process_logging import configure_process_logging

    configure_process_logging("INFO")  # the foreground Server's terminal is its log

    from openprogram.interfaces.server import start_web

    port = args.port
    if port is None:
        from openprogram.operations.worker.lifecycle import resolve_worker_port
        port = resolve_worker_port()
    thread = start_web(port=port, open_browser=args.browser)

    print("Press Ctrl+C to stop.")  # noqa: T201 - terminal prompt
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopping the Server.")  # noqa: T201 - terminal status
        sys.exit(0)


if __name__ == "__main__":
    main()
