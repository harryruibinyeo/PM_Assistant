"""register_person / list_people / delete_person business logic - called by
the thin adapters in pmchaser/mcp/tools.py."""

from __future__ import annotations

from pmchaser.db import base as db_base
from pmchaser.db.models import Person
from pmchaser.domain.validation import generate_link_code
from pmchaser.integrations import telegram as telegram_integration
from pmchaser.repositories import people as people_repo
from pmchaser.serializers import person_summary

# link_code has a UNIQUE constraint (models.py), and the alphabet is 36
# chars over 6 positions (~2.18 billion combinations), so a collision on
# any single attempt is already vanishingly unlikely - this bound exists
# so a freak collision degrades to a clear error instead of an unhandled
# IntegrityError crashing the call, per the refactor plan's finding #3.
_MAX_LINK_CODE_ATTEMPTS = 5


def _generate_unique_link_code(session) -> str | None:
    for _ in range(_MAX_LINK_CODE_ATTEMPTS):
        candidate = generate_link_code()
        if people_repo.find_by_link_code(session, candidate) is None:
            return candidate
    return None


def register_person(name: str, telegram_username: str | None = None, role: str = "team_member") -> dict:
    with db_base.session_scope() as session:
        existing = people_repo.find_by_name(session, name)
        if existing is not None:
            return {
                "error": f"'{name}' is already registered (person_id={existing.id})."
            }

        link_code = _generate_unique_link_code(session)
        if link_code is None:
            return {
                "error": f"Could not generate a unique link code after "
                f"{_MAX_LINK_CODE_ATTEMPTS} attempts - please try again."
            }
        person = Person(
            name=name.strip(),
            telegram_username=telegram_username,
            role=role,
            link_code=link_code,
        )
        session.add(person)
        session.flush()
        link_url = telegram_integration.build_link_url(link_code)
        return {
            "person_id": person.id,
            "name": person.name,
            "role": person.role,
            "link_code": link_code,
            "link_url": link_url,
            "instructions": (
                f"Send {person.name} this link and ask them to tap it: {link_url}"
                if link_url
                else f"Ask {person.name} to send /start {link_code} to the bot."
            ),
        }


def list_people(role: str | None = None) -> list[dict]:
    with db_base.session_scope() as session:
        people = people_repo.list_people(session, role=role)
        open_counts = people_repo.count_open_tasks_by_owner_ids(session, [p.id for p in people])
        return [
            person_summary(session, p, open_task_count=open_counts.get(p.id, 0))
            for p in people
        ]


def delete_person(name: str) -> dict:
    with db_base.session_scope() as session:
        person = people_repo.find_by_name(session, name)
        if person is None:
            return {"error": f"No registered person named '{name}'."}
        task_count = people_repo.count_all_tasks(session, person.id)
        if task_count > 0:
            return {
                "error": f"'{person.name}' owns {task_count} task(s) — reassign them "
                "with update_task(owner_name=...) or remove them with delete_task "
                "before removing this person."
            }
        person_id = person.id
        person_name = person.name
        session.delete(person)
        return {"deleted": True, "person_id": person_id, "name": person_name}
