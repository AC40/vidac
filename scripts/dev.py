"""Start both development servers and stop them together on Ctrl-C."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    options = {"cwd": root, "start_new_session": os.name != "nt"}
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "vidac.api:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8741",
        ],
        **options,
    )
    try:
        web = subprocess.Popen(
            ["npm.cmd" if os.name == "nt" else "npm", "run", "dev:web"], **options
        )
    except OSError:
        api.terminate()
        api.wait(timeout=10)
        raise

    def terminate(process, force=False):
        if process.poll() is not None:
            return
        if os.name == "nt":
            process.kill() if force else process.terminate()
        else:
            # npm owns Vite descendants; stop the whole group, not only the wrapper.
            try:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                pass

    def stop(signum=None, frame=None):
        for process in (api, web):
            terminate(process)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while api.poll() is None and web.poll() is None:
            time.sleep(0.3)
    finally:
        stop()
        for process in (api, web):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                terminate(process, force=True)
                process.wait()


if __name__ == "__main__":
    main()
