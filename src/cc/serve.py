import functools
import http.server
import pathlib


def make_server(directory: str | pathlib.Path, port: int) -> http.server.ThreadingHTTPServer:
    """Build (but don't start) an HTTP server for `directory`, bound to 127.0.0.1:port.

    Binds to localhost only — this is meant to be reached via an SSH port
    forward (e.g. VS Code Remote-SSH's automatic port detection), not exposed
    on the network.
    """
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve_directory(directory: str | pathlib.Path, port: int) -> None:
    """Serve `directory` over HTTP on 127.0.0.1:port until interrupted (Ctrl+C)."""
    with make_server(directory, port) as httpd:
        print(f"Sirviendo {directory} en http://localhost:{port} — Ctrl+C para parar")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nParado.")
