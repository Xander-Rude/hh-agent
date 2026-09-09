import unittest

from app.gpu_guard import (
    GPUStatus,
    is_gpu_busy,
    parse_nvidia_smi_output,
)


class GPUGuardTests(unittest.TestCase):
    def test_parse_selected_gpu(self):
        status = parse_nvidia_smi_output(
            "0, 12, 2048, 12288\n1, 88, 7000, 8192\n",
            gpu_index=1,
        )

        self.assertEqual(status.index, 1)
        self.assertEqual(status.utilization_percent, 88)
        self.assertEqual(status.memory_used_mb, 7000)
        self.assertEqual(status.memory_total_mb, 8192)
        self.assertEqual(status.memory_free_mb, 1192)

    def test_high_gpu_utilization_defers(self):
        busy, reason = is_gpu_busy(
            GPUStatus(
                index=0,
                utilization_percent=97,
                memory_used_mb=5000,
                memory_total_mb=12288,
            )
        )

        self.assertTrue(busy)
        self.assertIn("97%", reason)

    def test_low_free_vram_with_active_gpu_defers(self):
        busy, reason = is_gpu_busy(
            GPUStatus(
                index=0,
                utilization_percent=55,
                memory_used_mb=10500,
                memory_total_mb=12288,
            )
        )

        self.assertTrue(busy)
        self.assertIn("free VRAM", reason)

    def test_resident_ollama_model_does_not_defer_when_gpu_is_idle(self):
        busy, reason = is_gpu_busy(
            GPUStatus(
                index=0,
                utilization_percent=3,
                memory_used_mb=10500,
                memory_total_mb=12288,
            )
        )

        self.assertFalse(busy)
        self.assertEqual(reason, "")

    def test_normal_idle_gpu_does_not_defer(self):
        busy, _ = is_gpu_busy(
            GPUStatus(
                index=0,
                utilization_percent=18,
                memory_used_mb=3500,
                memory_total_mb=12288,
            )
        )

        self.assertFalse(busy)

    def test_missing_requested_gpu_raises(self):
        with self.assertRaises(ValueError):
            parse_nvidia_smi_output(
                "0, 12, 2048, 12288\n",
                gpu_index=1,
            )


if __name__ == "__main__":
    unittest.main()
