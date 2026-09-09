from app.targeted_hunt.outreach import OUTREACH_STATUSES, OutreachPackage, render_package


def test_render_package_is_human_in_the_loop():
    package = OutreachPackage(
        case_id=1,
        vacancy_id=42,
        vacancy_title="Head of Delivery",
        company="Example",
        person_id=7,
        person_name="Иван Петров",
        person_title="CTO",
        score=91,
        confidence=0.86,
        rationale="руководит нужной функцией",
        contacts=((3, "telegram", "@example", True),),
    )
    text = render_package(package)
    assert "TARGETED HUNT — 91/100" in text
    assert "Иван Петров" in text
    assert "@example" in text
    assert "/outreach 42 | CONTACT_ID | sent" in text


def test_outreach_funnel_has_expected_terminal_and_progress_states():
    assert {"sent", "replied", "call", "interview", "final", "offer", "closed"} <= OUTREACH_STATUSES
