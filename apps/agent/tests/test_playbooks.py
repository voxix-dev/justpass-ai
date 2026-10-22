from agent.playbook import load_playbooks
from agent.prompts import ROLE_JOBS


def test_playbooks_load():
    books = load_playbooks()
    assert {"essay", "math"} <= set(books)


def test_every_playbook_ends_with_review():
    for book in load_playbooks().values():
        assert book.steps[-1].role == "review", book.name


def test_every_playbook_role_has_a_worker_prompt():
    for book in load_playbooks().values():
        assert {step.role for step in book.steps} <= set(ROLE_JOBS), book.name
