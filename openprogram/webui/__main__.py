"""
Allow running the web UI with: python -m agentic_web

Starts the server and keeps it alive until interrupted.
"""

import argparse
import signal
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="python -m agentic_web",
        description="Start the Agentic Programming web UI.",
    )
    parser.add_argument(
        "--port", "-p", type=int, default=18109,
        help="Port to serve on (default: 18109)",
    )
    # 默认不弹浏览器：18109 是 API 后端，真正的 UI 是 Next 前端
    # （:18100）。之前默认 webbrowser.open 导致每次重启后端都往用户
    # 浏览器里弹一个没内容的后端页。
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

    thread = start_web(port=args.port, open_browser=args.browser)

    print("Press Ctrl+C to stop.")
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopping web UI.")
        sys.exit(0)


if __name__ == "__main__":
    main()
