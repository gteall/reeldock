"""One API / Worker process, draining new claims as soon as SIGTERM arrives."""

import uvicorn


class Server(uvicorn.Server):
    def handle_exit(self, sig, frame):
        worker = getattr(self.config.app.state, "worker", None)
        if worker:
            worker.request_stop()
        super().handle_exit(sig, frame)


def serve(app):
    Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=8000,
            workers=1,
            access_log=False,
            server_header=False,
            proxy_headers=False,
            timeout_graceful_shutdown=20,
        )
    ).run()
