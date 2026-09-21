"""
Main entry point for Unified Gateway Server.
"""
import argparse
import sys
import uvicorn
try:
    from .config import get_or_create_unified_key
except (ImportError, ValueError):
    try:
        from gateway.config import get_or_create_unified_key
    except ImportError:
        from unified_gateway.gateway.config import get_or_create_unified_key


def main():
    parser = argparse.ArgumentParser(description="Unified Multi-Provider LLM Gateway")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    unified_key = get_or_create_unified_key()
    print("=" * 65)
    print("           UNIFIED MULTI-PROVIDER LLM GATEWAY")
    print("=" * 65)
    print(f"[*] Gateway Server:    http://{args.host}:{args.port}")
    print(f"[*] ONE UNIFIED KEY:   {unified_key}")
    print(f"[*] Models API:        http://{args.host}:{args.port}/v1/models")
    print(f"[*] Chat API:          http://{args.host}:{args.port}/v1/chat/completions")
    print(f"[*] Quota & Stats:     http://{args.host}:{args.port}/stats")
    print(f"[*] Swagger Docs:      http://{args.host}:{args.port}/docs")
    print("=" * 65)
    print("[*] Starting server...\n")

    app_target = "unified_gateway.gateway.server:app" if "unified_gateway" in sys.modules else "gateway.server:app"
    uvicorn.run(
        app_target,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )


if __name__ == "__main__":
    main()
