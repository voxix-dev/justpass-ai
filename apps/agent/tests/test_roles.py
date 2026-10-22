from agent.roles import OUTBOUND_TOOLS, PRIVATE_DATA_TOOLS, ROLE_TOOLS
from shared.models import Role


def test_every_role_has_a_tool_list():
    assert set(ROLE_TOOLS) == set(Role)


def test_no_role_has_private_data_and_a_way_out():
    for role, tools in ROLE_TOOLS.items():
        assert not (tools & PRIVATE_DATA_TOOLS and tools & OUTBOUND_TOOLS), role


def test_research_gets_no_user_data():
    assert not ROLE_TOOLS[Role.RESEARCH] & PRIVATE_DATA_TOOLS


def test_only_the_curator_writes_memory():
    writers = {role for role, tools in ROLE_TOOLS.items() if "update_memory" in tools}
    assert writers == {Role.CURATOR}
