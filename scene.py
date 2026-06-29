#!/usr/bin/env python3
"""Physical scene state for an active date/meeting.

During a live social meeting Cara keeps a small, persistent snapshot of the PHYSICAL
situation so she stays consistent turn to turn instead of re-deriving it from the transcript
(and losing it as the scene scrolls out of the context window). The snapshot is carried
forward verbatim and only changes when the dialogue changes it, explicitly or implicitly.

Tracked (content-agnostic — it tracks whatever physical facts get established):
  location, her_posture, his_position   — single short strings
  her_clothing, removed_clothing         — what's on her / what's come off (and where)
  items_in_play                          — props/objects in use (persist; introduced, never dropped)
  people_present                         — anyone else physically in the scene
  other_facts                            — anything physical that fits no slot

Hybrid update: a deterministic cue check (`likely_change`) gates a small JSON-only LLM call,
so most turns cost nothing and only a turn that plausibly moved things re-derives the state.
"""

STR_SLOTS = ("location", "her_posture", "his_position")
LIST_SLOTS = ("her_clothing", "removed_clothing", "items_in_play", "people_present", "other_facts")
SLOTS = STR_SLOTS + LIST_SLOTS

# Cues that a turn likely CHANGED the physical scene — movement/position/location, (un)dressing,
# introducing/handling an item, or a new person. RU + EN, casefolded substring match. Broad on
# purpose: a false positive costs one cheap updater call; a false negative lets the scene drift.
_CHANGE_CUES = (
    # movement / posture (ru)
    "ложис", "ложиш", "ложус", "лёг", "лег", "ляг", "встан", "вста", "сел", "сядь", "сижу",
    "сади", "наклон", "поверн", "переверн", "колен", "на живот", "на спин", "на бок",
    "сверху", "снизу", "сзади", "прижм", "обхват", "обним", "обня", "подойд", "подош",
    "иди ко мне", "иди сюда", "подвин", "приподн", "раздвин", "опустис", "поднимис",
    "обопр", "оседла", "развернис", "встал",
    # location (ru)
    "кроват", "диван", "спальн", "ванн", "в душ", "на пол", "на стол", "к столу", "кухн",
    "у окна", "в комнат", "перейд", "пошли в", "пойдём", "пойдем",
    # dress (ru)
    "сними", "снял", "снимаю", "разден", "раздет", "оголи", "обнаж", "надень", "надева",
    "одет", "трусик", "чулк", "плать", "халат", "бель", "лифчик", "бюстг",
    # items / props (ru)
    "достаю", "достал", "беру", "взял", "вставля", "вставил", "вынима", "вынул", "приношу",
    "принёс", "принес", "привяз", "пристёг", "пристег", "ввожу", "смазк", "игрушк", "вибратор",
    # people (ru)
    "приходит", "пришла", "пришёл", "пришел", "заходит", "зашла", "с нами", "присоедин",
    # movement / posture (en)
    "lie down", "lying", "lay down", "sit", "kneel", "turn over", "turn around", "bend",
    "stand", "get up", "on your back", "on your stomach", "on top", "behind you",
    "come here", "come to me", "straddle", "lean", "spread", "press against", "roll over",
    # location (en)
    "to the bed", "on the bed", "couch", "bedroom", "bathroom", "shower", "on the floor",
    "kitchen", "by the window", "let's go to", "move to", "lets go",
    # dress (en)
    "take off", "took off", "undress", "strip", "naked", "put on", "wearing", "panties",
    "stockings", "robe", "lingerie", "bra",
    # items / people (en)
    "take out", "took out", "bring", "brought", "insert", "grab", "tie", "strap", "lube",
    "toy", "joins", "joined", "comes in", "with us",
)


def _empty():
    state = {s: "" for s in STR_SLOTS}
    for s in LIST_SLOTS:
        state[s] = []
    return state


def likely_change(text):
    """True if the message plausibly changes the physical scene — the gate for spending an
    LLM updater call (movement / new position / location / (un)dressing / an item / a person)."""
    t = (text or "").casefold()
    return any(c in t for c in _CHANGE_CUES)


_UPDATER_SYSTEM = (
    "You maintain a compact PHYSICAL SCENE STATE for an ongoing scene between Cara and the boss "
    "(and anyone else present). Given the CURRENT state and the latest messages, return the "
    "UPDATED state as STRICT JSON only — no prose, no code fences. Keys exactly: "
    '{"location": "", "her_posture": "", "his_position": "", "her_clothing": [], '
    '"removed_clothing": [], "items_in_play": [], "people_present": [], "other_facts": []}.\n'
    "Rules:\n"
    "- KEEP every fact the latest messages did NOT change — carry it forward verbatim. Continuity is the point.\n"
    "- her_posture / his_position = exactly how each is positioned NOW. They change ONLY when the "
    "dialogue moves them (he repositions her, she shifts, they relocate) — carry the EXACT current "
    "pose forward otherwise; never spring a new pose out of nowhere. Treat a pose like clothing: it "
    "stays until something in the dialogue changes it.\n"
    "- her_clothing = what she's wearing NOW; when an item comes off, move it to removed_clothing "
    "(note where it ended up if said). Clothing changes ONLY when the dialogue changes it — never "
    "add or remove a garment on your own.\n"
    "- items_in_play = props/objects/toys currently in use. ADD one only when it's actually brought "
    "in or used; NEVER drop an item that's still in use; never invent an item that wasn't mentioned.\n"
    "- people_present = anyone physically in the scene besides the two (by name if given).\n"
    "- CHANGE a field only on an explicit or clearly-implied change; CLEAR a field (\"\" or []) only "
    "when it plainly no longer applies.\n"
    "- Track ONLY the physical situation — never feelings, mood, or what was said.\n"
    "- Short, factual values in the dialogue's language; each list at most 6 items. Output the FULL "
    "state object every time."
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
    fallback for any field it omits. Returns None when nothing usable came back (caller keeps
    the existing scene)."""
    import llm
    data = llm.parse_llm_json(reply)
    if not isinstance(data, dict):
        return None
    out = dict(current) if current else _empty()
    for s in STR_SLOTS:
        out.setdefault(s, "")
        if s in data:
            out[s] = str(data.get(s) or "").strip()[:120]
    for s in LIST_SLOTS:
        vals = out.get(s) or []
        if s in data and isinstance(data.get(s), list):
            vals = [str(x).strip()[:120] for x in data[s] if str(x).strip()][:6]
        out[s] = vals
    return out


_LABELS = {
    "ru": {"location": "Где вы", "her_posture": "Её поза", "his_position": "Его положение",
           "her_clothing": "На ней сейчас", "removed_clothing": "Снято", "items_in_play": "В игре",
           "people_present": "Рядом ещё", "other_facts": "Ещё"},
    "en": {"location": "Where", "her_posture": "Her posture", "his_position": "His position",
           "her_clothing": "She's wearing", "removed_clothing": "Removed", "items_in_play": "In play",
           "people_present": "Also present", "other_facts": "Also"},
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
    for s in LIST_SLOTS:
        vals = [str(x).strip() for x in (state.get(s) or []) if str(x).strip()]
        if vals:
            lines.append(f"  - {labels[s]}: " + "; ".join(vals))
    if not lines:
        return ""
    head = ("Физическая обстановка ПРЯМО СЕЙЧАС — держись её как данности, пока его сообщение её не "
            "изменит (тогда обнови и дальше держи новую). Ничего не меняется само собой: ПОЗУ и "
            "положение держи неизменными, пока их не изменит диалог (никаких внезапных смен позы); "
            "одежду и предметы не вводи и не убирай без повода; не забывай, что уже в игре." if lang == "ru" else
            "The physical scene RIGHT NOW — treat it as given and stay consistent until his message "
            "changes it (then move to the new state and hold that). Nothing changes on its own: hold "
            "the POSE and positions exactly until the dialogue moves them (no sudden pose jumps); "
            "don't add or remove clothing/items without a cue, and never forget what's in play.")
    return head + "\n" + "\n".join(lines)
