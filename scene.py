#!/usr/bin/env python3
"""Physical scene state for an active date/meeting.

During a live social meeting Cara keeps a small, persistent snapshot of the PHYSICAL
situation — where they are, postures/positions, what's in play, state of dress — so she
stays consistent turn to turn instead of re-deriving it from the transcript (and losing
it as the scene grows past the context window). The snapshot is carried forward verbatim
and only changes when the dialogue changes it, explicitly or implicitly. Content-agnostic:
it tracks whatever physical facts get established.

Hybrid update: a deterministic cue check (`likely_change`) gates a small JSON-only LLM
call, so most turns cost nothing and only a turn that plausibly moved things re-derives
the state.
"""

# Ordered slots. All are short strings except other_facts (a small list).
STR_SLOTS = ("location", "her_posture", "his_position", "state_of_dress", "objects_in_play")
SLOTS = STR_SLOTS + ("other_facts",)

# Cues that a turn likely CHANGED the physical placement — movement verbs, position/location
# words, (un)dressing. RU + EN, casefolded substring match. Deliberately broad: a false
# positive just spends one cheap updater call; a false negative would let the scene drift.
_CHANGE_CUES = (
    # movement / posture (ru)
    "ложис", "ложиш", "ложус", "лёг", "лег", "ляг", "встан", "вста", "сел", "сядь", "сижу",
    "сади", "наклон", "поверн", "переверн", "колен", "на живот", "на спин", "на бок",
    "сверху", "снизу", "сзади", "прижм", "обхват", "обним", "обня", "подойд", "подош",
    "иди ко мне", "иди сюда", "подвин", "приподн", "раздвин", "опустис", "поднимис",
    "обопр", "оседла", "развернис",
    # location (ru)
    "кроват", "диван", "спальн", "ванн", "в душ", "на пол", "на стол", "к столу", "кухн",
    "у окна", "в комнат", "перейд", "пошли в", "пойдём", "пойдем", "встал",
    # dress (ru)
    "сними", "снял", "снимаю", "разден", "раздет", "оголи", "обнаж", "надень", "одет",
    "трусик", "чулк", "плать", "халат", "бель",
    # movement / posture (en)
    "lie down", "lying", "lay down", "sit", "kneel", "turn over", "turn around", "bend",
    "stand", "get up", "on your back", "on your stomach", "on top", "behind you",
    "come here", "come to me", "straddle", "lean", "spread", "press against", "roll over",
    # location (en)
    "to the bed", "on the bed", "couch", "bedroom", "bathroom", "shower", "on the floor",
    "kitchen", "by the window", "let's go to", "move to", "lets go",
    # dress (en)
    "take off", "took off", "undress", "strip", "naked", "put on", "wearing", "panties",
    "stockings", "robe", "lingerie",
)


def _empty():
    state = {s: "" for s in STR_SLOTS}
    state["other_facts"] = []
    return state


def likely_change(text):
    """True if the message plausibly changes the physical scene (movement / new position /
    location / (un)dressing) — the gate for spending an LLM updater call."""
    t = (text or "").casefold()
    return any(c in t for c in _CHANGE_CUES)


_UPDATER_SYSTEM = (
    "You maintain a compact PHYSICAL SCENE STATE for an ongoing scene between two people "
    "(Cara and the boss). You are given the CURRENT state and the latest messages. Return the "
    "UPDATED state as STRICT JSON only — no prose, no code fences. Keys exactly: "
    '{"location": "", "her_posture": "", "his_position": "", "state_of_dress": "", '
    '"objects_in_play": "", "other_facts": []}.\n'
    "Rules:\n"
    "- KEEP every fact the latest messages did NOT change — carry it forward verbatim. Continuity matters.\n"
    "- CHANGE a field only when the dialogue changes it, explicitly ('let's move to the couch') or "
    "implicitly (a new position or location is described).\n"
    "- CLEAR a field (empty string) only when it clearly no longer applies.\n"
    "- Track ONLY the physical situation (where they are, postures/positions, what is in play, "
    "state of dress) — never feelings, mood, or what was said.\n"
    "- Keep each value short and factual (a few words), in the dialogue's language. other_facts: up "
    "to 4 short physical facts that fit no other slot.\n"
    "- Output the FULL state object every time."
)


def build_update_messages(current, recent_turns, lang):
    """Messages for the JSON scene-updater: the current state + the latest few turns."""
    import json
    cur = json.dumps(current or _empty(), ensure_ascii=False)
    convo = "\n".join(
        f"{'Boss' if r['role'] == 'boss' else 'Cara'}: {r['text']}" for r in recent_turns)
    user = (f"CURRENT state:\n{cur}\n\nLatest messages:\n{convo}\n\n"
            "Return the updated state as JSON.")
    return [{"role": "system", "content": _UPDATER_SYSTEM},
            {"role": "user", "content": user}]


def parse_update(reply, current):
    """Parse the updater's JSON into a clean state dict, merged onto `current` as the safe
    fallback for any field it omits. Returns None when nothing usable came back (the caller
    then keeps the existing scene)."""
    import llm
    data = llm.parse_llm_json(reply)
    if not isinstance(data, dict):
        return None
    out = dict(current) if current else _empty()
    for s in STR_SLOTS:
        out.setdefault(s, "")
        if s in data:
            out[s] = str(data.get(s) or "").strip()[:120]
    facts = out.get("other_facts") or []
    if "other_facts" in data and isinstance(data.get("other_facts"), list):
        facts = [str(f).strip()[:120] for f in data["other_facts"] if str(f).strip()][:4]
    out["other_facts"] = facts
    return out


_LABELS = {
    "ru": {"location": "Где вы", "her_posture": "Её поза", "his_position": "Его положение",
           "state_of_dress": "Одежда", "objects_in_play": "В ходу", "other_facts": "Ещё"},
    "en": {"location": "Where", "her_posture": "Her posture", "his_position": "His position",
           "state_of_dress": "Dress", "objects_in_play": "In play", "other_facts": "Also"},
}


def render(state, lang):
    """Compact context block of the established physical scene, or '' if nothing is set."""
    if not state:
        return ""
    labels = _LABELS.get(lang, _LABELS["en"])
    lines = []
    for s in STR_SLOTS:
        v = (state.get(s) or "").strip()
        if v:
            lines.append(f"  - {labels[s]}: {v}")
    facts = state.get("other_facts") or []
    if facts:
        lines.append(f"  - {labels['other_facts']}: " + "; ".join(facts))
    if not lines:
        return ""
    head = ("Физическая обстановка ПРЯМО СЕЙЧАС — держись её в ответе как данности, пока его "
            "сообщение её не изменит (тогда обнови и дальше держи новую):" if lang == "ru" else
            "The physical scene RIGHT NOW — treat it as given and stay consistent in your reply "
            "until his message changes it (then move to the new state and hold that):")
    return head + "\n" + "\n".join(lines)
