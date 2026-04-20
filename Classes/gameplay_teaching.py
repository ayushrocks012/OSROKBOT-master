"""Central gameplay teaching profiles used by supervised OSROKBOT runs.

This module keeps operator-facing teaching prompts and planner-facing gameplay
doctrine in one place so early supervised runs can be taught consistently
without scattering game-specific heuristics across UI, task decomposition, and
planning code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_TEACHING_PROFILE = "guided_general"


@dataclass(frozen=True, slots=True)
class TeachingProfile:
    """One reusable gameplay teaching profile.

    Attributes:
        name: Stable identifier stored in config and runtime context.
        title: Operator-facing label shown in the supervisor UI.
        summary: High-level explanation of the workflow this profile teaches.
        questions: Early-run questions the operator should answer in teaching
            mode so the planner can align to the real game workflow.
        doctrine: Ordered gameplay rules the planner should prefer.
        success_signals: Screen outcomes that indicate the workflow progressed.
        avoid: Common mistakes or unsafe transitions the planner should avoid.
    """

    name: str
    title: str
    summary: str
    questions: tuple[str, ...]
    doctrine: tuple[str, ...]
    success_signals: tuple[str, ...]
    avoid: tuple[str, ...]


PROFILE_CATALOG: dict[str, TeachingProfile] = {
    "guided_general": TeachingProfile(
        name="guided_general",
        title="Guided General",
        summary="Teach the planner the core controls and screen transitions you use most often.",
        questions=(
            "How do you tell city view from map view in your current UI layout?",
            "Which key or button do you use to open the world map?",
            "Which key or button do you use to open the search or find panel?",
            "Which buttons usually confirm the desired action and send the march?",
            "What on-screen cues tell you the action worked?",
        ),
        doctrine=(
            "Prefer deterministic screen transitions before inventing pointer targets.",
            "Use L1 approval and learn from corrected targets instead of blind retries.",
            "Treat city-view UI, map-view UI, and search-panel UI as distinct states.",
            "When the screen evidence disagrees with the taught workflow, wait or ask for Fix.",
        ),
        success_signals=(
            "Map-only action buttons are visible after leaving city view.",
            "The search panel shows the expected resource or target categories.",
            "A march indicator, gather status, or attack status confirms the action started.",
        ),
        avoid=(
            "Do not confuse quest, research, event, or building panels with gameplay progression.",
            "Do not repeat the same failed transition indefinitely.",
            "Do not select OCR-only digits or decorative UI text as gameplay targets.",
        ),
    ),
    "gather_resources": TeachingProfile(
        name="gather_resources",
        title="Gather Resources",
        summary="Teach the standard gathering loop for food, wood, stone, and gold nodes.",
        questions=(
            "How do you move from city view to world map in your setup?",
            "How do you open the resource search panel once on the map?",
            "How do you choose resource type and level in the search panel?",
            "Which button starts gathering and which button sends the march?",
            "How do you verify the march actually left the city?",
        ),
        doctrine=(
            "Check city view versus map view first; if still in city, use the taught map transition.",
            "After reaching map view, open the resource search panel before scanning random map nodes.",
            "Choose the requested resource type before clicking Gather or Send.",
            "Confirm gathering only after the target node and gather controls are visible.",
            "Verify march departure before considering the gather workflow complete.",
        ),
        success_signals=(
            "Search panel shows Food, Wood, Stone, or Gold options.",
            "Gather and Send or March controls are visible in sequence.",
            "An active march slot, timer, or occupation indicator confirms the node is being gathered.",
        ),
        avoid=(
            "Do not click city buildings, quest widgets, or chat bubbles while teaching gathering.",
            "Do not treat city-production text like Iron Ore as proof that map-resource search is open.",
            "Do not skip directly from city view to node selection without verifying the map transition.",
        ),
    ),
    "gather_gems": TeachingProfile(
        name="gather_gems",
        title="Gather Gems",
        summary="Teach the map-search and march flow for gem gathering.",
        questions=(
            "How do you open the map and the gem search flow in your client?",
            "Which filters or categories identify gem deposits?",
            "Which gather or send controls are different for gems, if any?",
            "What screen signals confirm the gem march started successfully?",
        ),
        doctrine=(
            "Use the same city-to-map and map-to-search transitions as standard gathering unless the operator notes say otherwise.",
            "Prefer gem-specific search or filter controls over arbitrary node scanning.",
            "Confirm the target is a gem deposit before sending the march.",
        ),
        success_signals=(
            "Gem-related search, node, gather, or march indicators are visible.",
            "The march leaves the city and the node becomes occupied or timed.",
        ),
        avoid=(
            "Do not substitute wood, food, stone, or gold nodes for gem deposits.",
            "Do not spend time dragging across the map when the taught gem search workflow is available.",
        ),
    ),
    "farm_barbarians": TeachingProfile(
        name="farm_barbarians",
        title="Farm Barbarians",
        summary="Teach the search, target, attack, and send flow for barbarian farming.",
        questions=(
            "How do you open barbarian search or locate barbarian targets from map view?",
            "How do you choose barbarian level safely?",
            "Which button initiates the attack flow and which confirms the march?",
            "What tells you the march was sent and action points were spent appropriately?",
        ),
        doctrine=(
            "Reach map view first, then use the taught barbarian search or targeting flow.",
            "Verify the target is a barbarian before clicking attack or send.",
            "Check march-slot and action-point context before committing to the attack.",
            "Treat attack and send as separate confirmation steps unless the operator notes say otherwise.",
        ),
        success_signals=(
            "Barbarian search, attack, or march controls are visible.",
            "March status, combat travel, or reduced action points confirms the attack flow started.",
        ),
        avoid=(
            "Do not confuse resource nodes with barbarians.",
            "Do not send a march when action points or march slots are unavailable.",
        ),
    ),
    "map_navigation": TeachingProfile(
        name="map_navigation",
        title="Map Navigation",
        summary="Teach panning, scrolling, and target acquisition on the world map.",
        questions=(
            "How do you tell when the map is ready for panning?",
            "Which drag directions or gestures do you use to scroll to new areas?",
            "Which labels or OCR text indicate you have reached the desired map region?",
        ),
        doctrine=(
            "Confirm map view before issuing drag or scroll actions.",
            "Use bounded drags to pan the map rather than random clicks.",
            "Re-observe after each drag and stop if the screen no longer matches the taught navigation goal.",
        ),
        success_signals=(
            "Map-only controls stay visible while the background terrain changes.",
            "Expected region labels, nodes, or target controls appear after panning.",
        ),
        avoid=(
            "Do not drag while still in city view.",
            "Do not keep dragging after the desired region or target already appears.",
        ),
    ),
}


def profile_options() -> list[tuple[str, str]]:
    """Return stable `(name, title)` pairs for the supervisor UI."""

    return [(profile.name, profile.title) for profile in PROFILE_CATALOG.values()]


def get_profile(name: str | None) -> TeachingProfile:
    """Return the requested profile or the default when the name is unknown."""

    normalized_name = str(name or "").strip().lower()
    return PROFILE_CATALOG.get(normalized_name, PROFILE_CATALOG[DEFAULT_TEACHING_PROFILE])


def teaching_questions_text(profile_name: str | None) -> str:
    """Render the operator-facing teaching questions for one profile."""

    profile = get_profile(profile_name)
    lines = [f"Teaching prompts for {profile.title}:"]
    lines.extend(f"- {question}" for question in profile.questions)
    return "\n".join(lines)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mission_focus_hint(mission: str) -> str:
    goal_text = _clean_text(mission).lower()
    hints: list[str] = []
    if "wood" in goal_text:
        hints.append("Prefer wood nodes over other resource types.")
    if "food" in goal_text:
        hints.append("Prefer food nodes over other resource types.")
    if "stone" in goal_text:
        hints.append("Prefer stone nodes over other resource types.")
    if "gold" in goal_text:
        hints.append("Prefer gold nodes over other resource types.")
    if "gem" in goal_text:
        hints.append("Treat gem deposits as the target resource, not standard nodes.")
    if "barbarian" in goal_text:
        hints.append("Use barbarian targeting and attack controls, not gather controls.")
    if "scroll" in goal_text or "pan" in goal_text:
        hints.append("Use drag-based map navigation once map view is confirmed.")
    if "level 4" in goal_text or "lv4" in goal_text or "level four" in goal_text:
        hints.append("Prefer level 4 targets when the taught search flow exposes target level.")
    return " ".join(hints)


def build_teaching_brief(
    *,
    enabled: bool,
    profile_name: str | None,
    operator_notes: str = "",
    mission: str = "",
) -> str:
    """Build the planner-facing gameplay teaching brief for the active run.

    Args:
        enabled: Whether teaching mode is active for the run.
        profile_name: Selected gameplay profile.
        operator_notes: Free-form operator notes entered in the supervisor UI.
        mission: Current mission text used for mission-specific hints.

    Returns:
        str: One concise prompt block consumed by task decomposition and the
        planner, or an empty string when teaching mode is disabled.
    """

    if not enabled:
        return ""

    profile = get_profile(profile_name)
    mission_focus = _mission_focus_hint(mission)
    cleaned_notes = _clean_text(operator_notes)

    lines = [
        "Teaching mode is active. Follow the taught gameplay workflow before inventing new controls.",
        f"Gameplay profile: {profile.title}.",
        f"Profile summary: {profile.summary}",
        "Preferred workflow:",
    ]
    lines.extend(f"- {rule}" for rule in profile.doctrine)
    if mission_focus:
        lines.append(f"Mission-specific focus: {mission_focus}")
    lines.append("Expected success signals:")
    lines.extend(f"- {signal}" for signal in profile.success_signals)
    lines.append("Avoid these mistakes:")
    lines.extend(f"- {item}" for item in profile.avoid)
    if cleaned_notes:
        lines.append(f"Operator notes: {cleaned_notes}")
    else:
        lines.append("Operator notes: none yet. Stay conservative and prefer L1 learning over risky guesses.")
    return "\n".join(lines)
