"""
Aegis AI — Launcher
Start the Aegis AI server or console agent.

Usage:
    python start.py              Start the web server (default, port 8484)
    python start.py --console    Start the console chat agent
    python start.py --port 9090  Start the web server on a custom port
"""

import sys
import argparse


def main():
    parser = argparse.ArgumentParser(description="Aegis AI Launcher")
    parser.add_argument("--console", action="store_true", help="Run console chat instead of web server")
    parser.add_argument("--host", default="0.0.0.0", help="Server bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8484, help="Server port (default: 8484)")
    args = parser.parse_args()

    if args.console:
        from core.agent import run
        run()
    else:
        import uvicorn
        from server.app import app
        from core.config import CONFIG
        from core.personality.pack_loader import load_personality_pack, get_agent_display_name

        pack = load_personality_pack(CONFIG.get("active_personality_pack", "default"))
        agent_name = get_agent_display_name(pack)

        print()
        print("=" * 50)
        print("  AEGIS AI SERVER")
        print("=" * 50)
        print(f"  Agent: {agent_name}")
        print(f"  Local: http://localhost:{args.port}")
        print(f"  Network: http://<your-ip>:{args.port}")
        print(f"  API docs: http://localhost:{args.port}/docs")
        print("=" * 50)
        print()

        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
