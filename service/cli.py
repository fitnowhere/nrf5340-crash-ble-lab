import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="Run the self-hosted Zephyr diagnostic service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", default=os.getenv("DIAG_SERVICE_DATA", "service/data"))
    parser.add_argument("--token", default=os.getenv("DIAG_SERVICE_TOKEN"))
    args = parser.parse_args()
    if not args.token or len(args.token) < 16:
        parser.error("set --token or DIAG_SERVICE_TOKEN to at least 16 characters")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("direct server supports loopback only; use a TLS reverse proxy for remote access")
    os.environ["DIAG_SERVICE_TOKEN"] = args.token
    os.environ["DIAG_SERVICE_DATA"] = args.data
    import uvicorn
    uvicorn.run("service.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
