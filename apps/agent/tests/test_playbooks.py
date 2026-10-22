from agent.playbook import load_playbooks


def test_playbooks_load():
    books = load_playbooks()
    assert {"essay", "math"} <= set(books)


def test_every_playbook_ends_with_review():
    for book in load_playbooks().values():
        assert book.steps[-1].role == "review", book.name
