"""AgentHR CLI — start the server or manage agents from the command line."""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="agenthr",
        description="HR for AI agents — workforce management for agent fleets",
    )
    sub = parser.add_subparsers(dest="command")

    # serve
    serve_p = sub.add_parser("serve", help="Start the AgentHR API server")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8420)
    serve_p.add_argument("--reload", action="store_true")

    # init
    sub.add_parser("init", help="Initialize the database")

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "agenthr.api:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    elif args.command == "init":
        import asyncio
        from agenthr.database import create_tables

        asyncio.run(create_tables())
        print("AgentHR database initialized.")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
