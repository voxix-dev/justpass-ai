from agent import roles as agent_roles
from shared import roles


def test_the_api_and_the_agent_use_the_same_permissions():
    """Until agent/roles.py re-exports shared.roles, the two copies must match. Delete then."""
    assert roles.ROLE_TOOLS == agent_roles.ROLE_TOOLS
    assert roles.PRIVATE_DATA_TOOLS == agent_roles.PRIVATE_DATA_TOOLS
    assert roles.OUTBOUND_TOOLS == agent_roles.OUTBOUND_TOOLS
