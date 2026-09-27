import unittest

import apply_worker as worker


class HHResponseLetterPayloadGuardTests(unittest.TestCase):
    def setUp(self):
        self.boundary = "----WebKitFormBoundaryTEST"
        self.content_type = (
            "multipart/form-data; boundary="
            + self.boundary
        )

    def _body(self, *, letter: str | None = None) -> bytes:
        parts = [
            (
                f"--{self.boundary}\r\n"
                'Content-Disposition: form-data; name="resume_hash"\r\n\r\n'
                "resume123\r\n"
            ),
            (
                f"--{self.boundary}\r\n"
                'Content-Disposition: form-data; name="vacancy_id"\r\n\r\n'
                "137766371\r\n"
            ),
            (
                f"--{self.boundary}\r\n"
                'Content-Disposition: form-data; name="letterRequired"\r\n\r\n'
                "false\r\n"
            ),
        ]
        if letter is not None:
            parts.append(
                f"--{self.boundary}\r\n"
                'Content-Disposition: form-data; name="letter"\r\n\r\n'
                f"{letter}\r\n"
            )
        parts.append(f"--{self.boundary}--\r\n")
        return "".join(parts).encode("utf-8")

    def test_instant_apply_payload_gets_exact_letter(self):
        letter = "Здравствуйте!\n\nТочный текст письма."
        guarded = worker._multipart_upsert_text_field(
            self._body(),
            self.content_type,
            "letter",
            letter,
        )
        boundary = worker._multipart_boundary(self.content_type)

        self.assertIsNotNone(boundary)
        self.assertEqual(
            worker._multipart_field_value(
                guarded,
                boundary,
                "letter",
            ).decode("utf-8"),
            letter,
        )
        self.assertIn(b'name="vacancy_id"', guarded)
        self.assertIn(b"137766371", guarded)
        self.assertIn(b'name="letterRequired"', guarded)
        self.assertIn(b"false", guarded)

    def test_existing_wrong_letter_is_replaced_not_duplicated(self):
        guarded = worker._multipart_upsert_text_field(
            self._body(letter="старый текст"),
            self.content_type,
            "letter",
            "новый текст",
        )
        boundary = worker._multipart_boundary(self.content_type)

        self.assertEqual(
            worker._multipart_field_value(
                guarded,
                boundary,
                "letter",
            ).decode("utf-8"),
            "новый текст",
        )
        self.assertEqual(
            guarded.count(
                b'Content-Disposition: form-data; name="letter"'
            ),
            1,
        )

    def test_missing_boundary_blocks_guarded_mutation(self):
        with self.assertRaises(ValueError):
            worker._multipart_upsert_text_field(
                self._body(),
                "multipart/form-data",
                "letter",
                "text",
            )


if __name__ == "__main__":
    unittest.main()
