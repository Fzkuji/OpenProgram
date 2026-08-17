"""
Allow running the web UI with: python -m openprogram.webui

Starts the server and keeps it alive until interrupted.
"""

import argparse
import signal
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="python -m openprogram.webui",
        description="Start the OpenProgram web UI.",
    )
    parser.add_argument(
        "--port", "-p", type=int, default=None,
        help="Port to serve on (default: resolved single port, 18100)",
    )
    # 默认不弹浏览器；--browser 可以弹（单端口后本进程就是完整 UI）。
    parser.add_argument(
        "--browser", action="store_true",
        help="Open a browser window at the backend port after start",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help=argparse.SUPPRESS,  # 兼容旧参数；现在本来就是默认行为
    )
    args = parser.parse_args()

    from openprogram.webui import start_web

    port = args.port
    if port is None:
        from openprogram.worker.lifecycle import resolve_worker_port
        port = resolve_worker_port()
    thread = start_web(port=port, open_browser=args.browser)

    print("Press Ctrl+C to stop.")
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopping web UI.")
        sys.exit(0)


if __name__ == "__main__":
    main()
