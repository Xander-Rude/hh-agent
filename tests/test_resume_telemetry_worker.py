import unittest

from resume_telemetry_worker import _metric


class ResumeTelemetryMetricTests(unittest.TestCase):
    def test_parses_number_before_label(self):
        self.assertEqual(_metric("Просмотры 1 234", ("просмотр",)), 1234)

    def test_parses_number_after_label(self):
        self.assertEqual(_metric("17 приглашений", ("приглашен", "приглашени")), 17)

    def test_returns_none_when_metric_absent(self):
        self.assertIsNone(_metric("Резюме обновлено сегодня", ("просмотр",)))


if __name__ == "__main__":
    unittest.main()
