import io

from backfill_hard_filter_appeals import configure_console_encoding


class ReconfigurableStream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


def test_configure_console_encoding_uses_utf8_and_safe_errors():
    stdout = ReconfigurableStream()
    stderr = ReconfigurableStream()

    configure_console_encoding(stdout=stdout, stderr=stderr)

    assert stdout.calls == [{"encoding": "utf-8", "errors": "backslashreplace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "backslashreplace"}]


def test_configure_console_encoding_tolerates_stream_without_reconfigure():
    configure_console_encoding(stdout=io.StringIO(), stderr=io.StringIO())
