"""Submission outcome diagnostics for the approved native HH questionnaire POST.

Read-only: records response *metadata* and visible validation UI, never the
payload, question answers, cover letter, cookies, URLs with query strings, or
HTML response bodies. Does not submit/retry/reclassify applications.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlsplit


_HH_POST_PATH = "/applicant/vacancy_response"
_SENSITIVE = re.compile(
    r"(https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"(?:\+?\d[\d ()-]{7,}\d))", re.I,
)


def is_hh_questionnaire_post(request) -> bool:
    """Match native POST; ignore assets, unrelated requests and redirects."""
    try:
        parsed = urlsplit(str(request.url))
        return (
            str(request.method).upper() == "POST"
            and parsed.scheme == "https"
            and parsed.hostname in {"hh.ru", "www.hh.ru"}
            and parsed.path.rstrip("/") == _HH_POST_PATH
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _public_path(value: str, base: str) -> str:
    """Save redirect *path* only, never secret parameters or fragments."""
    try:
        parsed = urlsplit(urljoin(base, value))
        if parsed.hostname not in {"hh.ru", "www.hh.ru"}:
            return "(external)"
        path = parsed.path or "/"
        # Avoid logging identifiers that may occur in arbitrary path segments.
        return re.sub(r"\d{5,}", "{id}", path)[:110]
    except (TypeError, ValueError):
        return "(invalid)"


def _safe_hint(text: str) -> str:
    # Visible validation text can occasionally echo applicant input.
    compact = " ".join(str(text or "").split())
    return _SENSITIVE.sub("[redacted]", compact)[:150]


class HHQuestionnairePostObservation:
    def __init__(self) -> None:
        self.http_statuses: list[int] = []
        self.redirect_paths: list[str] = []
        self.request_failures: list[str] = []
        self.finished_count = 0
        self.wait_outcome = "not_waited"

    def response(self, response) -> None:
        try:
            request = response.request
            if not is_hh_questionnaire_post(request):
                return
            if len(self.http_statuses) >= 5:
                return
            self.http_statuses.append(int(response.status))
            redirect = (response.headers or {}).get("location")
            if redirect:
                self.redirect_paths.append(_public_path(redirect, request.url))
        except (AttributeError, TypeError, ValueError):
            return

    def request_failed(self, request) -> None:
        try:
            if not is_hh_questionnaire_post(request):
                return
            if len(self.request_failures) >= 5:
                return
            # Playwright failure is a browser-specific error string;
            # keep only the stable error token, not user data or URLs.
            failure = str(request.failure or "")
            match = re.search(r"(?:net::)?ERR_[A-Z0-9_]+", failure)
            self.request_failures.append(match.group(0).removeprefix("net::") if match else "request_failed")
        except (AttributeError, TypeError, ValueError):
            return

    def request_finished(self, request) -> None:
        """requestfinished follows the complete response body / redirect."""
        if is_hh_questionnaire_post(request):
            self.finished_count += 1

    def wait_for_terminal(
        self, page, *, post_seen: bool, blocked: bool,
        timeout_ms: int = 30000, poll_ms: int = 250,
    ) -> str:
        """Allow the native form POST to finish BEFORE navigating away.

        Playwright callbacks run while its synchronous event loop is pumped.
        A response header by itself is insufficient: only requestfinished or
        requestfailed is terminal. A missing outcome stays fail-closed.
        """
        if blocked:
            self.wait_outcome = "blocked"
            return self.wait_outcome
        if not post_seen:
            self.wait_outcome = "no_post"
            return self.wait_outcome
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while True:
            if self.finished_count:
                self.wait_outcome = "finished"
                break
            if self.request_failures:
                self.wait_outcome = "failed"
                break
            remain_ms = int((deadline - time.monotonic()) * 1000)
            if remain_ms <= 0:
                self.wait_outcome = "timeout"
                break
            try:
                page.wait_for_timeout(min(max(1, poll_ms), remain_ms))
            except Exception:
                # Page navigation/closure is possible during native form POST.
                # Preserve ambiguity; never retry automatically.
                self.wait_outcome = "page_unavailable"
                break
        return self.wait_outcome

    @staticmethod
    def visible_errors(page) -> dict:
        """Read only error-specific elements; exclude values and page HTML."""
        try:
            result = page.evaluate(r"""() => {
              const form = document.querySelector('form[name="vacancy_response"]');
              const visible = (el) => {
                for (let n = el; n; n = n.parentElement) {
                  const s = getComputedStyle(n);
                  const r = n.getBoundingClientRect();
                  if (s.display === 'none' || s.visibility === 'hidden' ||
                      r.width === 0 || r.height === 0 ||
                      (s.maxHeight !== 'none' && parseFloat(s.maxHeight) === 0)) {
                    return false;
                  }
                }
                return true;
              };
              const selector = [
                '[role="alert"]', '[aria-invalid="true"]',
                '[data-qa*="error"]', '[data-qa*="validation"]',
                '[data-qa*="captcha"]'
              ].join(',');
              // Keep errors scoped to this submission form: global nav,
              // language switchers or unrelated page banners are NOT HH
              // questionnaire validation errors.
              const nodes = form
                ? [...form.querySelectorAll(selector)].filter(visible)
                : [];
              return {
                form_present: !!form,
                error_count: nodes.length,
                // Read only error messages, never field values / innerHTML.
                hints: nodes.filter(el => !['INPUT','TEXTAREA','SELECT'].includes(el.tagName))
                  .slice(0, 6).map(el => (el.innerText || '').trim().slice(0, 180))
                  .filter(Boolean),
                invalid_names: nodes.filter(el => el.getAttribute('aria-invalid') === 'true'
                  && ['INPUT','TEXTAREA','SELECT'].includes(el.tagName))
                  .slice(0, 10).map(el => (/^(?:task_\d+(?:_text)?|letter|text|resume(?:_id)?)$/
                    .test(el.getAttribute('name') || '') ? 'known_field' : 'other_field')),
                submit_enabled: !!(form && [...form.querySelectorAll('button[type="submit"]')]
                  .some(el => !el.disabled && visible(el)))
              };
            }""")
        except Exception:
            return {"snapshot_error": "read_failed"}
        if not isinstance(result, dict):
            return {"snapshot_error": "unsupported"}
        # The form may contain a hidden warning in the DOM; we only count
        # actually visible errors, never infer failure from invisible text.
        return {
            "form_present": bool(result.get("form_present")),
            "submit_enabled": bool(result.get("submit_enabled")),
            "error_count": min(99, max(0, int(result.get("error_count") or 0))),
            "hints": [_safe_hint(item) for item in (result.get("hints") or [])[:6]],
            "invalid_names": list((result.get("invalid_names") or [])[:10]),
        }

    def summary(self, ui: dict | None = None) -> str:
        ui = ui or {}
        # Avoid long/secret console logs. Only coarse status and sanitized UI.
        parts = [
            f"wait_outcome={self.wait_outcome}",
            f"request_finished={self.finished_count}",
            f"http_statuses={self.http_statuses}",
            f"redirect_paths={self.redirect_paths}",
            f"request_failures={self.request_failures}",
            f"form_present={ui.get('form_present', '?')}",
            f"submit_enabled={ui.get('submit_enabled', '?')}",
            f"ui_errors={ui.get('error_count', '?')}",
            f"invalid_fields={ui.get('invalid_names', [])}",
            f"validation_hints={ui.get('hints', [])}",
        ]
        return " ".join(parts)
