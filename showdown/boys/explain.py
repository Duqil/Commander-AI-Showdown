"""Why did a Boy choose that? Reasons for the viewer's "Reasons" log tab.

A Boy scores every option as a sum of inputs times weights (brain.py) and
takes the best one. So the honest answer to "why this and not that?" is: the
inputs where the chosen option scored more than the runner-up. We show the
top few, in plain words, plus:

* how sure he was (his chance of picking it), and
* whether he followed SimpleBot's pick ("follows"), went his own way
  ("own call"), or had no hint to go by ("no hint": SimpleBot had no pick
  for this kind of choice, or the hint was hidden while training).
  The viewer says it in words: "Same as SimpleBot", "His own choice
  (SimpleBot: ...)", "No SimpleBot advice".

This is what drove the choice, not a plan or a feeling: a Boy has no plan
beyond the decision in front of him.
"""

from __future__ import annotations

from showdown.boys import cards, net

HINT = "simple_prior"

# Plain words for common inputs. Others are shown by name, with spaces.
LABELS = {
    "forge_would_play": "Forge's AI likes it",
    "pass_on_my_main": "passing in my main phase",
    "land_drop": "a land drop",
    "spell_on_my_main": "casting in my main phase",
    "creature_spell": "a creature",
    "cast_commander": "my commander",
    "keeps_instant_up": "keeps mana for an instant",
    "taps_out_holding_instant": "taps out while holding an instant",
    "target_is_leader": "the biggest threat",
    "target_threat_rank": "further down the threat list",
    "target_killable": "can be finished off",
    "target_low_life": "low on life",
    "target_bad_block": "a blocker would kill it",
    "target_trade_block": "a blocker would trade with it",
    "target_double_block": "two blockers would kill it",
    "target_blockers_outnumbered": "more attackers than blockers",
    "target_weak": "not a threat right now",
    "attack_breaks_deal": "breaks a deal",
    "attack_gang_target": "the player we ganged up on",
    "attack_revenge": "revenge for a broken deal",
    "attack_push_back": "pushing back at a gang-up",
    "attack_valuable_at_risk": "a valuable creature could die",
    "attack_last_creatures_at_risk": "one of my last creatures could die",
    "stay_home": "staying home",
    "hurt_partner": "hurts a deal partner",
    "target_ward_unpayable": "ward I can't pay",
    "target_commander": "a commander",
    "mode_lasting": "lasting value",
    "mode_impulse_now": "card playable now",
    "mode_impulse_next_turn": "card playable next turn",
    "mode_impulse_wasted": "the card would likely be wasted",
    "keep_good_lands": "a good number of lands",
    "mulligan_deeper": "another mulligan costs a card",
    "attack": "attacking at all",
    "attack_small": "a small attacker",
    "attack_grudge": "a grudge",
    "target_blockers": "has blockers",
    "play_in_combat": "playing during combat",
    "pass_in_fight": "passing during a fight",
    "trick_my_unblocked": "a pump for my unblocked attacker",
    "pass_keeping_instant_mana": "keeping mana for an instant",
    "instant_speed_spell": "an instant-speed spell",
    "instant_removal_no_rush": "removal that could wait",
    "forge_suggested": "Forge's AI suggests it",
    "ability_forge_would_play": "an ability Forge's AI would use",
    "ability": "using an ability",
    "ability_on_my_turn": "using an ability on my turn",
    "keep_color_coverage": "covers my colors",
    "keep_land_distance": "close to the ideal land count",
    "pick_land_new_color": "a new color",
    "pick_land_color_value": "colors my cards need",
    "home_power": "a good blocker to keep home",
}


def label(name: str) -> str:
    if cards.is_card_feature(name):
        return "card memory"
    return LABELS.get(name, name.replace("_", " "))


def explain(brain, kind: str, options: list[dict], picked: int, probs: list[float],
            labels: list[str] | None = None) -> dict | None:
    """One reason record, or None when there was nothing to choose."""
    if len(options) < 2:
        return None
    name = (lambda i: labels[i]) if labels and len(labels) == len(options) else (lambda i: f"option {i + 1}")
    scores = [brain.score(kind, o) for o in options]
    runner = max((i for i in range(len(options)) if i != picked), key=lambda i: scores[i])
    simple = next((i for i, o in enumerate(options) if o.get(HINT)), None)
    # Hidden hint (training only) or no pick from SimpleBot: nothing to follow.
    follow = "no hint" if simple is None else "follows" if simple == picked else "own call"
    w = brain.weights.get(kind, {})
    from showdown.boys.brain import DEFAULT_WEIGHTS  # here: brain imports a lot

    def parts(o: dict) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, v in o.items():
            if k == HINT or (cards.is_card_feature(k) and not brain.card_memory):
                continue
            out[label(k)] = out.get(label(k), 0.0) + w.get(k, DEFAULT_WEIGHTS.get(k, 0.0)) * v
        if brain.neural:  # the neural add-on (net.py): each pair of inputs is a reason of its own
            for (a, b), v in net.pairs(brain.nets.get(kind), kind, o).items():
                key = f"{label(a)} together with {label(b)}"
                out[key] = out.get(key, 0.0) + v
        return out

    mine, theirs = parts(options[picked]), parts(options[runner])
    diff = {k: mine.get(k, 0.0) - theirs.get(k, 0.0) for k in set(mine) | set(theirs)}
    reasons = []
    for k, v in sorted(diff.items(), key=lambda kv: -kv[1])[:3]:
        if v < 0.3:
            break
        # Mostly a minus on the 2nd choice: it's a reason against that one.
        against = theirs.get(k, 0.0) < 0 and abs(theirs.get(k, 0.0)) > abs(mine.get(k, 0.0))
        reasons.append((k, round(v, 1), "against" if against else "for"))
    return {"kind": kind, "picked": name(picked), "runner_up": name(runner),
            "sure": round(probs[picked], 2), "follow": follow,
            "simple_pick": name(simple) if simple is not None and simple != picked else None,
            "reasons": reasons}


KIND_WORDS = {"play": "Plays", "attack": "Attacks", "target": "Targets", "mode": "Chooses mode",
              "mulligan": "Mulligan", "pick_card": "Picks", "keyword_cost": "Extra cost"}


def strength(v: float) -> str:
    return "strong" if v >= 3 else "medium" if v >= 1 else "slight"


def sentence(r: dict) -> str:
    """Readable lines, e.g.:
    Attacks: Weaver at 4-Nahiri. His own choice (SimpleBot: Weaver at 1-Riku). 74% sure.
    Reason: the biggest threat (strong). Against the 2nd choice, Weaver at 1-Riku: a blocker would kill it (strong).
    """
    who = {"follows": "Same as SimpleBot", "no hint": "No SimpleBot advice"}.get(
        r["follow"], f"His own choice (SimpleBot: {r.get('simple_pick')})")
    text = f"{KIND_WORDS.get(r['kind'], r['kind'])}: {r['picked']}. {who}. {round(r['sure'] * 100)}% sure."
    pro = [f"{k} ({strength(v)})" for k, v, side in r["reasons"] if side == "for"]
    con = [f"{k} ({strength(v)})" for k, v, side in r["reasons"] if side == "against"]
    if pro:
        text += " Reason: " + ", ".join(pro) + "."
    if con:
        text += f" Against the 2nd choice, {r['runner_up']}: " + ", ".join(con) + "."
    if not pro and not con:
        text += f" Only just ahead of the 2nd choice, {r['runner_up']}."
    return text
