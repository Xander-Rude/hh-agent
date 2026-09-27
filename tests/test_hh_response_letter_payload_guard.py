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


class _FakeRequest:
    def __init__(self, body: bytes, content_type: str):
        self.method = "POST"
        self.headers = {"content-type": content_type}
        self.post_data_buffer = body


class _FakeRoute:
    def __init__(self, request):
        self.request = request
        self.continued = []
        self.aborted = []

    def continue_(self, **kwargs):
        self.continued.append(kwargs)

    def abort(self, reason):
        self.aborted.append(reason)


class _FakePage:
    def __init__(self):
        self.handler = None

    def route(self, _pattern, handler):
        self.handler = handler

    def unroute(self, _pattern, _handler):
        self.handler = None

    def wait_for_timeout(self, _timeout):
        pass


class HHTwoStepNativeSubmitGuardTests(unittest.TestCase):
    boundary = "----WebKitFormBoundaryFINAL"
    content_type = "multipart/form-data; boundary=" + boundary

    def _body(self, *, field_name: str = "text", value: str = "") -> bytes:
        return (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'
            f"{value}\r\n"
            f"--{self.boundary}\r\n"
            'Content-Disposition: form-data; name="vacancy_id"\r\n\r\n'
            "136790942\r\n"
            f"--{self.boundary}--\r\n"
        ).encode("utf-8")

    def test_two_step_final_submit_is_verified_but_not_rewritten(self):
        letter = "Здравствуйте!\n\nТочный текст письма."
        page = _FakePage()
        route = _FakeRoute(
            _FakeRequest(
                self._body(field_name="text", value=letter),
                self.content_type,
            )
        )

        def action():
            page.handler(route)
            return {"clicked": True}

        _result, state = worker._guard_hh_response_post(
            page,
            letter,
            action,
            mutate_letter=False,
        )

        self.assertTrue(state["post_seen"])
        self.assertTrue(state["letter_verified"])
        self.assertFalse(state["letter_injected"])
        self.assertFalse(state["blocked"])
        self.assertEqual(route.continued, [{}])
        self.assertEqual(route.aborted, [])

    def test_two_step_final_submit_is_blocked_when_letter_is_missing(self):
        letter = "Здравствуйте!\n\nТочный текст письма."
        page = _FakePage()
        route = _FakeRoute(
            _FakeRequest(
                self._body(field_name="other", value="not the letter"),
                self.content_type,
            )
        )

        def action():
            page.handler(route)
            return {"clicked": True}

        _result, state = worker._guard_hh_response_post(
            page,
            letter,
            action,
            mutate_letter=False,
        )

        self.assertTrue(state["post_seen"])
        self.assertFalse(state["letter_verified"])
        self.assertTrue(state["blocked"])
        self.assertEqual(route.continued, [])
        self.assertEqual(route.aborted, ["blockedbyclient"])


if __name__ == "__main__":
    unittest.main()
