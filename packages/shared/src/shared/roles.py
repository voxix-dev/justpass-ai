"""Which tools each role may call. The model can only act through tools, so this list is the
permission system: the agent offers only these tools and the Context API refuses the rest.
Changing it needs owner review (see CODEOWNERS)."""

from shared.models import Role

PRIVATE_DATA_TOOLS = frozenset(
    {"get_profile", "get_subject_memory", "list_history", "search_history", "update_memory"}
)
# Anything that can carry data out of the sandbox. A URL counts.
OUTBOUND_TOOLS = frozenset({"web_search", "web_fetch"})

ROLE_TOOLS: dict[Role, frozenset[str]] = {
    Role.ORCHESTRATOR: frozenset(
        {"run_agent", "get_profile", "get_subject_memory", "list_history", "search_history"}
    ),
    Role.RESEARCH: frozenset({"web_search", "web_fetch", "write_artifact"}),
    Role.WRITER: frozenset({"read_artifact", "get_profile", "write_artifact"}),
    Role.SOLVER: frozenset({"run_code", "write_artifact"}),
    Role.REVIEW: frozenset({"read_artifact"}),
    Role.CURATOR: frozenset({"get_profile", "get_subject_memory", "update_memory"}),
}


def tools_for(role: Role) -> frozenset[str]:
    return ROLE_TOOLS[role]
