"""Regression checks for the Task Scheduler's windowless Ollama launcher."""

import io
import subprocess

import ollama_headless


def test_launcher_uses_create_no_window_and_keeps_parent_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama_headless, "ROOT", tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    executable = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    calls = []

    class FakeServer:
        pid = 42

        def __init__(self):
            self.stdout = io.BytesIO(b"listening on 127.0.0.1:11434\n")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stdout.close()

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeServer()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert ollama_headless.run_server() == 0
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [str(executable), "serve"]
    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["stdout"] == subprocess.PIPE
    log_text = (tmp_path / "logs" / "ollama_server.log").read_text(encoding="utf-8")
    assert "listening on 127.0.0.1:11434" in log_text
    assert "Ollama serve exited code=0" in log_text


def test_missing_ollama_is_logged_without_console(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama_headless, "ROOT", tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert ollama_headless.run_server() == 1
    assert "executable not found" in (
        tmp_path / "logs" / "ollama_server.log"
    ).read_text(encoding="utf-8")
