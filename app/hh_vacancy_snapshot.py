from __future__ import annotations

import hashlib
import json
import base64
import gzip
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db import Vacancy, VacancySourceSnapshot


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def capture_vacancy_dom_snapshot(page) -> dict[str, Any]:
    """Capture semantic card content that may exist only in HH's web UI."""

    script = r"""
() => {
  const root = document.querySelector("main") || document.body;
  const canonical = document.querySelector('link[rel="canonical"]');

  const raw = Array.from(root.querySelectorAll("[data-qa]")).map((el) => ({
    data_qa: el.getAttribute("data-qa") || "",
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.textContent || "").trim(),
    href: el instanceof HTMLAnchorElement ? el.href : null,
    title: el.getAttribute("title"),
    aria_label: el.getAttribute("aria-label"),
  }));

  const seen = new Set();
  const data_qa = [];
  for (const item of raw) {
    if (!item.text && !item.href && !item.title && !item.aria_label) continue;
    const key = JSON.stringify(item);
    if (seen.has(key)) continue;
    seen.add(key);
    data_qa.push(item);
  }

  const json_ld = Array.from(
    document.querySelectorAll('script[type="application/ld+json"]')
  ).map((el) => (el.textContent || "").trim()).filter(Boolean);

  const meta = Array.from(
    document.querySelectorAll("meta[name], meta[property]")
  ).map((el) => ({
    name: el.getAttribute("name"),
    property: el.getAttribute("property"),
    content: el.getAttribute("content"),
  })).filter((item) => item.content);

  return {
    url: window.location.href,
    canonical_url: canonical ? canonical.href : null,
    document_title: document.title,
    main_text: (root.innerText || root.textContent || "").trim(),
    main_html: root.outerHTML || "",
    data_qa,
    json_ld,
    meta,
  };
}
"""

    try:
        result = page.evaluate(script)
        if isinstance(result, dict):
            # Preserve the raw card DOM without bloating SQLite with plain HTML.
            # The exact HTML can be reconstructed byte-for-byte from this field.
            main_html = str(result.pop("main_html", "") or "")
            if main_html:
                result["main_html_gzip_b64"] = base64.b64encode(
                    gzip.compress(
                        main_html.encode("utf-8"),
                        compresslevel=6,
                    )
                ).decode("ascii")
            return result
    except Exception as exc:
        return {
            "capture_error": f"{type(exc).__name__}: {exc}",
        }

    return {}


def build_hh_source_payload(
    *,
    hh_id: str,
    api_payload: dict[str, Any] | None,
    dom_snapshot: dict[str, Any] | None,
    api_error: str | None = None,
    collected_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the persisted wrapper while keeping the HH API object untouched."""

    captured = collected_at or _utcnow_naive()
    return {
        "schema_version": "hh-vacancy-source-v1",
        "source": "hh",
        "hh_id": str(hh_id),
        "collected_at": captured.isoformat(),
        "api": api_payload,
        "api_error": api_error,
        "dom": dom_snapshot or {},
    }


def source_payload_content_hash(payload: dict[str, Any]) -> str:
    """Hash source content while ignoring collection-time bookkeeping."""

    stable = {
        "schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "hh_id": payload.get("hh_id"),
        "api": payload.get("api"),
        "dom": payload.get("dom"),
    }
    return hashlib.sha256(
        _json_dumps(stable).encode("utf-8")
    ).hexdigest()


def extract_key_skills(payload: dict[str, Any]) -> list[str]:
    """Return employer-authored HH key-skill tags from API, with DOM fallback."""

    skills: list[str] = []
    api_payload = payload.get("api")

    if isinstance(api_payload, dict):
        raw_skills = api_payload.get("key_skills") or []
        if isinstance(raw_skills, list):
            for item in raw_skills:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                else:
                    name = str(item or "").strip()

                if name and name not in skills:
                    skills.append(name)

    if skills:
        return skills

    dom_payload = payload.get("dom")
    if not isinstance(dom_payload, dict):
        return skills

    for item in dom_payload.get("data_qa") or []:
        if not isinstance(item, dict):
            continue
        data_qa = str(item.get("data_qa") or "").lower()
        if data_qa not in {
            "skills-element",
            "vacancy-skill",
            "vacancy-skill-element",
        }:
            continue
        name = " ".join(str(item.get("text") or "").split()).strip()
        if name and name not in skills:
            skills.append(name)

    return skills


def _parse_hh_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)

    return parsed


def extract_published_at(payload: dict[str, Any]) -> datetime | None:
    api_payload = payload.get("api")
    if not isinstance(api_payload, dict):
        return None

    return _parse_hh_datetime(
        api_payload.get("published_at")
        or api_payload.get("created_at")
    )


def extract_salary_fields(
    payload: dict[str, Any],
) -> tuple[int | None, int | None, str | None]:
    api_payload = payload.get("api")
    if not isinstance(api_payload, dict):
        return None, None, None

    salary = api_payload.get("salary")
    if not isinstance(salary, dict):
        return None, None, None

    def to_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    currency = str(salary.get("currency") or "").strip() or None
    return (
        to_int(salary.get("from")),
        to_int(salary.get("to")),
        currency,
    )


def collect_hh_source_payload(
    *,
    page,
    hh_id: str,
) -> dict[str, Any]:
    """Capture the loaded HH vacancy card without relying on api.hh.ru."""

    dom_snapshot = capture_vacancy_dom_snapshot(page)

    return build_hh_source_payload(
        hh_id=hh_id,
        api_payload=None,
        dom_snapshot=dom_snapshot,
        api_error="not_requested: DOM is the primary HH source",
    )


def record_vacancy_source_snapshot(
    session,
    vacancy: Vacancy,
    payload: dict[str, Any],
) -> bool:
    """Store latest raw payload and append history only when content changes."""

    payload_json = _json_dumps(payload)
    payload_hash = source_payload_content_hash(payload)
    collected_at = (
        _parse_hh_datetime(payload.get("collected_at"))
        or _utcnow_naive()
    )

    vacancy.source_payload_json = payload_json
    vacancy.source_payload_hash = payload_hash
    vacancy.source_payload_collected_at = collected_at
    vacancy.key_skills_json = _json_dumps(extract_key_skills(payload))

    published_at = extract_published_at(payload)
    if published_at is not None:
        vacancy.published_at = published_at

    salary_from, salary_to, salary_currency = extract_salary_fields(payload)
    if salary_from is not None:
        vacancy.salary_from = salary_from
    if salary_to is not None:
        vacancy.salary_to = salary_to
    if salary_currency is not None:
        vacancy.salary_currency = salary_currency

    existing = session.scalars(
        select(VacancySourceSnapshot.id)
        .where(
            VacancySourceSnapshot.vacancy_id == vacancy.id,
            VacancySourceSnapshot.payload_hash == payload_hash,
        )
        .limit(1)
    ).first()

    if existing is not None:
        return False

    session.add(
        VacancySourceSnapshot(
            vacancy_id=vacancy.id,
            source="hh",
            payload_hash=payload_hash,
            payload_json=payload_json,
            collected_at=collected_at,
        )
    )
    return True
