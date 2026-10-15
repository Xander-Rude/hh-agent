"""Keep the Ollama server alive without creating a visible Windows console."""

import logging
import os
import subprocess
from logging.handlers import RotatingFileHandler
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_server() -> int:
    log_path = ROOT / "logs" / "ollama_server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hh_agent.ollama_server")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

    try:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            logger.error("LOCALAPPDATA is not defined")
            return 1
        executable = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        if not executable.is_file():
            logger.error("Ollama executable not found: %s", executable)
            return 1

        # pythonw.exe has no console; CREATE_NO_WINDOW is essential for its
        # console-subsystem child (ollama.exe). Keep this parent alive so Task
        # Scheduler's IgnoreNew / restart policy still applies to the server.
        with subprocess.Popen(
            [str(executable), "serve"],
            cwd=str(Path.home()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            logger.info("Ollama serve started pid=%s", process.pid)
            if process.stdout is not None:
                for raw_line in process.stdout:
                    logger.info("%s", raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
            return_code = process.wait()
            logger.info("Ollama serve exited code=%s", return_code)
            return return_code
    except Exception:
        logger.exception("Ollama launcher failed")
        return 1
    finally:
        logger.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(run_server())
