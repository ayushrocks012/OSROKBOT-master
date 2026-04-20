from gameplay_teaching import (
    DEFAULT_TEACHING_PROFILE,
    build_teaching_brief,
    get_profile,
    profile_options,
    teaching_questions_text,
)


def test_profile_options_expose_operator_visible_titles():
    options = profile_options()

    assert options
    assert (DEFAULT_TEACHING_PROFILE, get_profile(DEFAULT_TEACHING_PROFILE).title) in options


def test_build_teaching_brief_includes_profile_doctrine_and_operator_notes():
    brief = build_teaching_brief(
        enabled=True,
        profile_name="gather_resources",
        operator_notes="From city press Space, then press F, then choose Wood, Gather, and Send.",
        mission="Gather wood safely.",
    )

    assert "Teaching mode is active." in brief
    assert "Gameplay profile: Gather Resources." in brief
    assert "Prefer wood nodes over other resource types." in brief
    assert "Operator notes: From city press Space, then press F, then choose Wood, Gather, and Send." in brief


def test_teaching_questions_text_renders_profile_questions():
    prompt_text = teaching_questions_text("farm_barbarians")

    assert "Teaching prompts for Farm Barbarians:" in prompt_text
    assert "How do you open barbarian search" in prompt_text
