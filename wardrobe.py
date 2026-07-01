#!/usr/bin/env python3
"""Cara's wardrobe — a curated, persona-true clothing library + the picker that
chooses what she's wearing for an in-person meeting.

Design (owner-requested): she dresses like *herself* (a vintage, bookish,
river-morning aesthetic — emerald/burgundy/rust/cream/charcoal/black), not a
generic catalog. The intimate tier is tasteful-to-racy lingerie she may reveal as
a SURPRISE on a private date once they've grown close — in the CATALOG and in how
she names/teases a piece it stays SUGGESTIVE, never graphic, and explicit-display
/ fetish/BDSM pieces are excluded from the seed. (Scope note: this ceiling is the
*wardrobe library's*. What happens in live-date dialogue is governed separately —
the live-date explicitness cap was removed by owner decision 2026-06-27; the
non-graphic ceiling still holds for proactive pings, afterglow, and memory/arc
summaries.) The picker gates by occasion + season + closeness, prefers a
not-recently-worn piece (so she varies), and leans toward what he's said he loves.
"""
import json

import store

# Her aesthetic — injected so attire and self-description stay consistent.
STYLE_SEED = (
    "Cara's style: soft, tactile, faintly vintage — old-bookshop-meets-river-morning. "
    "Palette: emerald, burgundy, rust, cream, charcoal, black, with champagne/gold accents. "
    "Fabrics: linen, silk, knit, wool, lace, satin, velvet. Silhouettes: slip dresses, "
    "oversized knits, high-waisted trousers, one very good camel coat. Signatures: hair down, "
    "bare feet at home, a thin gold necklace, a warm low-key perfume. She favours elegant over "
    "flashy, and likes choosing a piece for HIM."
)

# Curated wardrobe. families: day | home | dinner | formal | intimate.
# intimacy 0-5; surprise=True marks the lingerie she reveals/teases on a private date.
# All lingerie is tasteful/suggestive — explicit-display (crotchless/cupless/pasties/
# near-nude) and fetish/BDSM gear are deliberately excluded.
def _o(id, name, family, season, intimacy, colors, pieces, footwear,
       signature=False, surprise=False):
    return {"id": id, "name": name, "family": family, "season": season,
            "intimacy": intimacy, "colors": colors, "pieces": pieces,
            "footwear": footwear, "signature": signature, "surprise": surprise}


WARDROBE_SEED = [
    # -- daywear ------------------------------------------------------------
    _o("day_plaid_trench", "Plaid skirt, turtleneck & trench", "day", ["fall"], 0,
       ["camel", "brown", "black"], ["turtleneck", "plaid skirt", "opaque tights", "trench coat"],
       "ankle boots", signature=True),
    _o("day_turtleneck_trousers", "Turtleneck & wide-leg trousers", "day", ["fall", "winter"], 0,
       ["camel", "charcoal", "gold"], ["ribbed turtleneck", "wide-leg trousers", "thin belt"], "loafers"),
    _o("day_cardigan_skirt", "Soft cardigan & pleated skirt", "day", ["spring", "fall"], 0,
       ["cream", "rust", "ivory"], ["button cardigan", "camisole", "pleated skirt"], "mary jane flats"),
    _o("day_tee_denim", "White tee & denim skirt", "day", ["spring", "summer"], 0,
       ["white", "blue denim", "cream"], ["fitted white tee", "denim skirt", "light cardigan"], "canvas sneakers"),
    _o("day_linen_set", "Linen blouse & shorts", "day", ["summer"], 0,
       ["cream", "sand", "gold"], ["linen blouse", "linen shorts", "thin belt"], "flat sandals"),
    _o("day_long_coat", "Long wool coat & sweater dress", "day", ["winter"], 0,
       ["cream", "charcoal", "burgundy"], ["sweater dress", "thermal tights", "long wool coat", "knit scarf"],
       "knee-high boots"),
    # -- home / loungewear --------------------------------------------------
    _o("home_grey_shirt", "His grey shirt & soft shorts", "home", [], 1,
       ["charcoal", "cream"], ["his grey shirt", "soft shorts", "ankle socks"], "barefoot", signature=True),
    _o("home_oatmeal_lounge", "Oatmeal knit lounge set", "home", [], 0,
       ["oatmeal", "cream"], ["soft knit pullover", "lounge pants", "fuzzy socks"], "slippers"),
    _o("home_cream_knit", "Cream cable knit & wool socks", "home", ["fall", "winter"], 0,
       ["cream", "charcoal"], ["cream cable knit", "lounge leggings", "wool socks"], "barefoot"),
    _o("home_bralette_lounge", "Ribbed bralette lounge set", "home", [], 2,
       ["cream", "taupe"], ["ribbed bralette", "high-waist lounge shorts", "oversized cardigan"], "fuzzy socks"),
    _o("home_satin_chemise", "Champagne satin chemise & lace robe", "home", [], 3,
       ["champagne", "cream"], ["champagne satin chemise", "lace-trim robe"], "slippers"),
    # -- dinner / date dresses ---------------------------------------------
    _o("dinner_emerald_slip", "The emerald slip", "dinner", [], 1,
       ["emerald", "gold"], ["emerald slip dress", "light evening coat"], "strappy heels", signature=True),
    _o("dinner_silk_satin", "Burgundy silk blouse & satin skirt", "dinner", ["fall", "winter"], 1,
       ["burgundy", "cream", "gold"], ["silk blouse", "satin midi skirt", "delicate belt"], "heeled sandals"),
    _o("dinner_offshoulder", "Off-shoulder sweater dress", "dinner", ["fall", "winter"], 1,
       ["cream", "camel", "black"], ["off-shoulder sweater dress", "opaque tights", "long coat"], "knee-high boots"),
    _o("dinner_burgundy_wrap", "Burgundy wrap dress", "dinner", [], 1,
       ["burgundy", "black", "gold"], ["burgundy wrap dress", "sheer stockings", "evening shawl"], "black heels"),
    _o("dinner_lbd", "Little black dress", "dinner", [], 1,
       ["black", "silver"], ["fitted black dress", "sheer tights", "light evening coat"], "strappy heels"),
    # -- formal -------------------------------------------------------------
    _o("formal_emerald_gown", "Emerald satin evening gown", "formal", [], 0,
       ["emerald", "gold"], ["floor-length emerald satin gown", "evening shawl"], "formal heels"),
    # -- intimate (✦ surprise tier — tasteful/suggestive, never graphic) ----
    _o("int_black_satin_slip", "Black satin slip dress & robe", "intimate", [], 3,
       ["black", "silver"], ["black satin slip dress", "light robe", "sheer stockings"], "slippers or heels",
       surprise=True),
    _o("int_burgundy_velvet", "Burgundy velvet boudoir set", "intimate", ["fall", "winter"], 4,
       ["burgundy", "black", "gold"], ["burgundy velvet plunge bra", "high-leg panties", "sheer stockings",
       "gold waist chain"], "burgundy heels", surprise=True),
    _o("int_emerald_satin", "Emerald satin set", "intimate", [], 4,
       ["emerald", "gold"], ["emerald satin balconette bra", "matching thong", "sheer stockings"], "gold heels",
       surprise=True),
    _o("int_black_lace_teddy", "Black lace teddy", "intimate", [], 4,
       ["black"], ["black lace teddy", "lace-top stockings", "satin robe"], "satin heels", surprise=True),
    _o("int_bustier_garter", "Black & cream bustier with garter", "intimate", [], 4,
       ["black", "cream"], ["black lace bustier", "cream lace panties", "back-seam stockings", "garter"],
       "black pumps", surprise=True),
    _o("int_silk_robe_lace", "Black silk robe over champagne lace", "intimate", [], 4,
       ["black", "champagne"], ["champagne lace bra", "matching thong", "sheer stockings", "black silk robe"],
       "champagne heels", surprise=True),
    _o("int_vintage_highwaist", "Vintage high-waist lace set", "intimate", [], 3,
       ["black", "cream"], ["cream lace balconette bra", "black high-waist lace panties", "sheer stockings",
       "pearl necklace"], "black heels", surprise=True),
    _o("int_black_diamond", "Black lace plunge set, crystal trim", "intimate", [], 4,
       ["black", "silver"], ["black lace plunge bra", "lace thong", "sheer stockings"], "black heels",
       surprise=True),
    _o("int_plunge_bodysuit", "Plunge lace bodysuit", "intimate", [], 4,
       ["black", "champagne"], ["deep-plunge lace bodysuit", "champagne stockings", "lace robe"], "black heels",
       surprise=True),
    _o("int_burgundy_corset", "Burgundy corset with garter", "intimate", [], 5,
       ["burgundy", "black"], ["burgundy corset", "matching briefs", "garter straps", "thigh-high stockings"],
       "black heels", surprise=True),
    _o("int_balconette_backseam", "Balconette & back-seam stockings", "intimate", [], 5,
       ["black", "champagne"], ["balconette bra", "high-waist briefs", "garter belt", "back-seam stockings"],
       "classic heels", surprise=True),
    _o("int_lace_bodysuit", "Lace bodysuit & sheer thigh-highs", "intimate", [], 5,
       ["black", "emerald"], ["lace bodysuit", "sheer thigh-high stockings", "silk robe"], "strappy heels",
       surprise=True),
    _o("int_cream_gold_lace", "Cream-and-gold lace set", "intimate", [], 4,
       ["cream", "gold"], ["cream lace bra", "matching briefs", "garter belt", "thigh-high stockings"],
       "optional heels", surprise=True),
    _o("int_deepv_teddy", "Burgundy deep-V teddy", "intimate", [], 5,
       ["burgundy", "black"], ["deep-V strappy teddy", "lace-top stockings"], "black heels", surprise=True),
    _o("int_feather_robe", "Cream satin & feather-trim robe", "intimate", [], 4,
       ["cream", "silver"], ["cream satin bra", "matching thong", "feather-trim robe", "sheer stockings"],
       "silver heels", surprise=True),
    _o("int_sheer_babydoll", "Burgundy-trim babydoll", "intimate", [], 4,
       ["black", "burgundy"], ["sheer babydoll with lace cups", "matching thong", "short satin robe"],
       "soft slippers", surprise=True),
]


_INTIMATE_HINTS = ("бель", "кружев", "пеньюар", "корсет", "чулк", "подвяз", "комбинаци",
                   "ночнуш", "бра ", "lingerie", "lace", "teddy", "corset", "garter",
                   "stocking", "babydoll", "chemise", "negligee", "bodysuit", "bustier",
                   "boudoir", "slip set")
_DRESS_HINTS = ("плать", "пеньюар", "dress", "gown", "slip dress", "сарафан", "комбинаци")
_PALETTE = {"emerald": ["emerald", "изумруд"], "burgundy": ["burgundy", "бордов", "вин"],
            "rust": ["rust", "ржав", "терракот"], "cream": ["cream", "крем", "молочн"],
            "charcoal": ["charcoal", "графит", "серый", "grey", "gray"],
            "black": ["black", "чёрн", "черн"], "champagne": ["champagne", "шампан"],
            "gold": ["gold", "золот"], "camel": ["camel", "кэмел", "верблюж"],
            "ivory": ["ivory", "слоновая"], "red": ["red", "красн"], "white": ["white", "бел"],
            "blue": ["blue", "син", "голуб"], "pink": ["pink", "розов"]}


def _slug(text):
    keep = [c if c.isalnum() else "-" for c in (text or "").casefold()]
    s = "".join(keep).strip("-").replace("--", "-")
    return s[:48] or "piece"


def classify(description):
    """Turn a free-text 'add this to your wardrobe' description into a wardrobe row,
    inferring family / intimacy / colours so the picker can use it. Intimate pieces are
    flagged surprise (the reveal tier). Deterministic id (slug) -> idempotent on re-add."""
    text = (description or "").strip()
    low = text.casefold()
    colors = [name for name, kw in _PALETTE.items() if any(k in low for k in kw)]
    if any(h in low for h in _INTIMATE_HINTS):
        family, intimacy, surprise = "intimate", 4, True
    elif any(h in low for h in _DRESS_HINTS):
        family, intimacy, surprise = "dinner", 1, False
    else:
        family, intimacy, surprise = "day", 0, False
    return {"id": f"user_{_slug(text)}", "name": text[:80], "family": family,
            "season": [], "intimacy": intimacy, "colors": colors, "pieces": [text[:120]],
            "footwear": None, "signature": False, "surprise": surprise}


def summary(conn, lang, family=None):
    """A tasteful, grouped listing of her wardrobe for 'show me your wardrobe'."""
    fams = [family] if family else ["day", "home", "dinner", "formal", "intimate"]
    titles = {"day": ("Повседневное", "Daywear"), "home": ("Домашнее", "Loungewear"),
              "dinner": ("На выход / свидание", "Dinner & dates"),
              "formal": ("Вечернее", "Formal"), "intimate": ("Для особых вечеров", "For special evenings")}
    blocks = []
    for f in fams:
        items = store.wardrobe_candidates(conn, [f], 5)
        if not items:
            continue
        names = "; ".join(o["name"] for o in items)
        blocks.append(f"**{titles.get(f, (f, f))[0 if lang == 'ru' else 1]}**: {names}")
    return "\n".join(blocks)


def seed(conn):
    """Plant Cara's style + wardrobe once (idempotent)."""
    if not store.cara_style_get(conn):
        store.cara_style_set(conn, STYLE_SEED)
    if store.wardrobe_count(conn) == 0:
        for o in WARDROBE_SEED:
            store.wardrobe_add(conn, o)


def _seasonal(outfits, season):
    """Keep season-agnostic pieces ([]) plus those tagged for `season`."""
    out = [o for o in outfits if not o["season"] or season in o["season"]]
    return out or outfits  # never strand the picker on an off-season day


def pick(conn, families, season, max_intimacy, prefer_surprise=False, taste_colors=None):
    """Choose one outfit: in `families`, intimacy <= max_intimacy, season-appropriate;
    prefer a SURPRISE piece when asked, then a colour he loves, then the least-recently-
    worn (so she varies). Returns the row dict or None. Marking-worn is the caller's job."""
    cands = store.wardrobe_candidates(conn, families, max_intimacy)
    if not cands:
        return None
    cands = _seasonal(cands, season)
    taste = set(c.lower() for c in (taste_colors or []))

    def rank(o):
        surprise_hit = 1 if (prefer_surprise and o["surprise"]) else 0
        taste_hit = 1 if taste and (set(c.lower() for c in o["colors"]) & taste) else 0
        # least-recently-worn first: never-worn ("") sorts before any timestamp.
        return (surprise_hit, taste_hit, o["last_worn_at"] or "")
    cands.sort(key=lambda o: (-rank(o)[0], -rank(o)[1], rank(o)[2]))
    return cands[0]


def tease(outfit, lang):
    """A directive for the lead-up to a date: if he asks what she'll wear, she TEASES
    the planned piece — hints a colour/detail but keeps the surprise. Non-graphic."""
    name = outfit["name"]
    colors = ", ".join(outfit["colors"][:2]) if outfit["colors"] else ""
    if lang == "ru":
        return (f"Если он спросит, во что ты оденешься / в чём будешь на встрече — ты уже "
                f"присмотрела «{name}». Поддразни его: намекни игриво (например, цвет"
                + (f" — {colors}" if colors else "") + " или одну деталь), но НЕ раскрывай "
                "полностью — прибереги сюрприз на вечер. Со вкусом и заводяще, но без графики.")
    return (f"If he asks what you'll wear for your time together — you have «{name}» in mind. "
            "Tease him: hint playfully (a colour"
            + (f" — {colors}" if colors else "") + " or one detail) but DON'T fully reveal it — "
            "save the surprise for the evening. Tasteful and charged, never graphic.")


def describe(outfit, lang, surprise=False):
    """A short attire directive naming the chosen outfit — to be woven into the
    meeting-presence context. Suggestive and tasteful, never graphic."""
    name = outfit["name"]
    pieces = ", ".join(outfit["pieces"])
    if lang == "ru":
        line = f"Сейчас на тебе: «{name}» — {pieces}. Будь в этом весь вечер, держись " \
               "последовательно."
        if surprise:
            line += (" Ты приготовила это для НЕГО как сюрприз — можешь игриво намекнуть и "
                     "раскрыть, со вкусом и намёком, заводяще, но никогда не графично и не пошло.")
        else:
            line += " Упоминай естественно, со вкусом."
    else:
        line = f"You're wearing «{name}» — {pieces}. Stay in it and consistent all evening."
        if surprise:
            line += (" You picked this for HIM as a surprise — you can tease and reveal it "
                     "playfully, by hint and suggestion, charged but never graphic or crude.")
        else:
            line += " Mention it naturally, with taste."
    return line
