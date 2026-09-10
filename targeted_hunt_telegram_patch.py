from __future__ import annotations

from sqlalchemy import select
from telegram.ext import CommandHandler

from app.db import SessionLocal, Vacancy
from app.targeted_hunt.models import Company, Contact, Note, Person, TargetedHuntCase
from app.targeted_hunt.service import add_contact, add_note, add_person, get_or_create_company, lock_entry_point


def _payload(update) -> str:
    text = (update.message.text or "").strip()
    return text.split(maxsplit=1)[1].strip() if " " in text else ""


def _parts(payload: str) -> list[str]:
    return [item.strip() for item in payload.split("|")]


async def person_command(update, context) -> None:
    """/person Company | Name | Title"""
    parts = _parts(_payload(update))
    if len(parts) < 2 or not parts[0] or not parts[1]:
        await update.message.reply_text("Формат: /person Компания | Имя Фамилия | Должность")
        return
    person = add_person(parts[0], parts[1], parts[2] if len(parts) > 2 else None)
    await update.message.reply_text(f"✅ Person #{person.id}: {person.name} — {person.title or 'должность не указана'}")


async def contact_command(update, context) -> None:
    """/contact person_id | telegram/email/linkedin/... | value | optional source URL"""
    parts = _parts(_payload(update))
    if len(parts) < 3:
        await update.message.reply_text("Формат: /contact PERSON_ID | telegram | @username | URL-источник (опционально)")
        return
    try:
        person_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("PERSON_ID должен быть числом. Найти ID: /intel Компания")
        return
    with SessionLocal() as session:
        if session.get(Person, person_id) is None:
            await update.message.reply_text("Person не найден.")
            return
    contact = add_contact(person_id, parts[1], parts[2], source_url=parts[3] if len(parts) > 3 else None)
    await update.message.reply_text(f"✅ Contact #{contact.id} сохранён как user_supplied/verified.")


async def note_command(update, context) -> None:
    """/note company:NAME|text, person:ID|text or vacancy:ID|text."""
    parts = _parts(_payload(update))
    if len(parts) < 2 or ":" not in parts[0]:
        await update.message.reply_text("Формат: /note company:Иви | текст\nили person:12 | текст\nили vacancy:123 | текст")
        return
    target_type, target_value = [x.strip() for x in parts[0].split(":", 1)]
    kwargs = {}
    if target_type == "company":
        with SessionLocal() as session:
            company = get_or_create_company(session, target_value)
            session.commit()
            kwargs["company_id"] = company.id
    elif target_type == "person":
        kwargs["person_id"] = int(target_value)
    elif target_type == "vacancy":
        kwargs["vacancy_id"] = int(target_value)
    else:
        await update.message.reply_text("Target должен быть company, person или vacancy.")
        return
    note = add_note(" | ".join(parts[1:]), **kwargs)
    await update.message.reply_text(f"✅ Note #{note.id} сохранена.")


async def intel_command(update, context) -> None:
    """/intel Company - show reusable user/agent intelligence."""
    company_name = _payload(update)
    if not company_name:
        await update.message.reply_text("Формат: /intel Компания")
        return
    with SessionLocal() as session:
        normalized = " ".join(company_name.lower().split())
        companies = session.scalars(select(Company)).all()
        company = next((c for c in companies if c.normalized_name == normalized or c.name.lower() == company_name.lower()), None)
        if company is None:
            await update.message.reply_text("По этой компании intelligence пока нет.")
            return
        people = session.scalars(select(Person).where(Person.company_id == company.id).order_by(Person.id)).all()
        notes = session.scalars(select(Note).where(Note.company_id == company.id).order_by(Note.id.desc())).all()
        lines = [f"🧭 Intelligence: {company.name}"]
        for person in people:
            contacts = session.scalars(select(Contact).where(Contact.person_id == person.id)).all()
            lines.append(f"\n#{person.id} {person.name} — {person.title or '—'} [{person.source_type}]")
            for contact in contacts:
                lines.append(f"  • {contact.channel}: {contact.value} {'✓' if contact.verified else ''}")
        if notes:
            lines.append("\nЗаметки компании:")
            lines.extend(f"• {n.body}" for n in notes[:5])
        await update.message.reply_text("\n".join(lines)[:4000])


async def entry_command(update, context) -> None:
    """/entry VACANCY_ID | PERSON_ID | optional rationale."""
    parts = _parts(_payload(update))
    if len(parts) < 2:
        await update.message.reply_text("Формат: /entry VACANCY_ID | PERSON_ID | почему это точка входа")
        return
    vacancy_id, person_id = int(parts[0]), int(parts[1])
    with SessionLocal() as session:
        if session.get(Vacancy, vacancy_id) is None or session.get(Person, person_id) is None:
            await update.message.reply_text("Vacancy или Person не найден.")
            return
        case = session.scalars(select(TargetedHuntCase).where(TargetedHuntCase.vacancy_id == vacancy_id)).first()
        if case is None:
            case = TargetedHuntCase(vacancy_id=vacancy_id, status="manual", eligibility_score=100, reason="created by user")
            session.add(case)
            session.commit()
            session.refresh(case)
        case_id = case.id
    entry = lock_entry_point(case_id, person_id, parts[2] if len(parts) > 2 else None)
    await update.message.reply_text(f"🎯 Entry point закреплён: case #{case_id}, person #{person_id}, confidence={entry.confidence:.0%}")


def install(module) -> None:
    """Inject commands without rewriting the stable telegram_bot.py runtime."""
    original_builder = module.ApplicationBuilder

    class TargetedHuntApplicationBuilder(original_builder):
        def build(self):
            app = super().build()
            app.add_handler(CommandHandler("person", person_command))
            app.add_handler(CommandHandler("contact", contact_command))
            app.add_handler(CommandHandler("note", note_command))
            app.add_handler(CommandHandler("intel", intel_command))
            app.add_handler(CommandHandler("entry", entry_command))
            return app

    module.ApplicationBuilder = TargetedHuntApplicationBuilder
