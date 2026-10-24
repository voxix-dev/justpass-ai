from agent.config import MODELS, OPUS, model_for
from shared.models import Role, Tier


def test_every_role_and_tier_has_a_model():
    assert set(MODELS) == {(role, tier) for role in Role for tier in Tier}


def test_orchestrator_is_always_opus():
    assert {model_for(Role.ORCHESTRATOR, tier) for tier in Tier} == {OPUS}


def test_no_sonnet_or_haiku():
    for model in MODELS.values():
        assert "sonnet" not in model and "haiku" not in model, model
