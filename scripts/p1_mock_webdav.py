"""Local-only generated directory fixture; reuses P0's HTTP fault server."""

import threading

from p0lib.mockdav import mock_server


def main():
    with mock_server() as server:
        server.files.update(
            {
                "/incoming": None,
                "/library": None,
                "/incoming/Example": None,
                "/incoming/Example/movie.mkv": b"fixture",
            }
        )
        print(server.url, flush=True)
        print("Generated in-memory fixture. No real NAS or media is connected.", flush=True)
        threading.Event().wait()


if __name__ == "__main__":
    main()
