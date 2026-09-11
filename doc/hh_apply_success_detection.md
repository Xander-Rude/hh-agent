# HH apply success detection

The HH UI can vary the confirmation wording shown after a successful application.

`apply_dispatcher.py` now extends the legacy worker's `SUCCESS_MARKERS` and `ALREADY_APPLIED_MARKERS` with several stable wording variants before the HH worker runs. This preserves the worker's conservative behavior while reducing false `manual_required` results caused only by changed confirmation copy.

No behavior changes were made for employer questions, tests, CAPTCHA, missing cover letters, or other genuine manual cases.
