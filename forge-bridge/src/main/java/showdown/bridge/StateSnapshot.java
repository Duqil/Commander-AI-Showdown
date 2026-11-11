package showdown.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.Game;
import forge.game.GameLogEntry;
import forge.game.card.Card;
import forge.game.card.CounterType;
import forge.game.combat.Combat;
import forge.game.player.Player;
import forge.game.GameObject;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.spellability.TargetChoices;
import forge.game.zone.ZoneType;

import java.util.List;
import java.util.Map;

/**
 * Turns Forge's game objects into plain JSON that Python can read.
 *
 * A snapshot is taken from one player's point of view: you see your own hand,
 * but only the hand SIZE of your opponents. That matters for The Boys later:
 * a learning bot must not be allowed to peek at hidden cards.
 *
 * The browser viewer gets a "spectator" snapshot instead (viewer = null):
 * everyone's hand is included, because the person watching may see it all.
 */
public final class StateSnapshot {
    private StateSnapshot() { }

    /** Everything, including every hand. Only for the viewer, never for a bot. */
    public static JsonObject spectator(Game game) {
        return of(game, null);
    }

    /** The game as {@code viewer} sees it. {@code viewer == null} means a spectator who sees all hands. */
    public static JsonObject of(Game game, Player viewer) {
        JsonObject state = new JsonObject();
        state.addProperty("turn", game.getPhaseHandler().getTurn());
        Player active = game.getPhaseHandler().getPlayerTurn();
        state.addProperty("active_player", active == null ? null : active.getName());
        var phase = game.getPhaseHandler().getPhase();
        state.addProperty("phase", phase == null ? null : phase.name()); // e.g. "MAIN1", "COMBAT_DECLARE_ATTACKERS"
        state.addProperty("you", viewer == null ? null : viewer.getName());
        state.addProperty("stack_size", game.getStack().size());
        state.add("stack", stack(game));

        JsonArray players = new JsonArray();
        for (Player p : game.getRegisteredPlayers()) {
            JsonObject pj = new JsonObject();
            pj.addProperty("name", p.getName());
            pj.addProperty("life", p.getLife());
            pj.addProperty("lost", p.hasLost());
            pj.addProperty("hand_size", p.getCardsIn(ZoneType.Hand).size());
            pj.addProperty("library_size", p.getCardsIn(ZoneType.Library).size());
            pj.add("battlefield", cards(p.getCardsIn(ZoneType.Battlefield)));
            pj.add("graveyard", cards(p.getCardsIn(ZoneType.Graveyard)));
            pj.add("command", cards(p.getCardsIn(ZoneType.Command)));
            pj.add("exile", cards(p.getCardsIn(ZoneType.Exile)));
            pj.addProperty("poison", p.getPoisonCounters());
            JsonArray cmdDamage = new JsonArray();
            for (Map.Entry<Card, Integer> e : p.getCommanderDamage()) {
                JsonObject dj = new JsonObject();
                dj.addProperty("commander", e.getKey().getName());
                dj.addProperty("owner", e.getKey().getOwner().getName());
                dj.addProperty("damage", e.getValue());
                cmdDamage.add(dj);
            }
            pj.add("commander_damage", cmdDamage); // damage TAKEN from each commander
            if (viewer == null || p == viewer) {
                pj.add("hand", cards(p.getCardsIn(ZoneType.Hand)));
            }
            players.add(pj);
        }
        state.add("players", players);
        return state;
    }

    public static JsonArray cards(Iterable<Card> cards) {
        JsonArray arr = new JsonArray();
        for (Card c : cards) {
            arr.add(card(c));
        }
        return arr;
    }

    /** Keywords that let a card be used from the graveyard. */
    private static final java.util.Set<forge.game.keyword.Keyword> GRAVEYARD_KEYWORDS = java.util.EnumSet.of(
            forge.game.keyword.Keyword.FLASHBACK, forge.game.keyword.Keyword.ESCAPE,
            forge.game.keyword.Keyword.JUMP_START, forge.game.keyword.Keyword.MADNESS,
            forge.game.keyword.Keyword.UNEARTH, forge.game.keyword.Keyword.DISTURB,
            forge.game.keyword.Keyword.EMBALM, forge.game.keyword.Keyword.ETERNALIZE,
            forge.game.keyword.Keyword.ENCORE, forge.game.keyword.Keyword.RETRACE,
            forge.game.keyword.Keyword.SCAVENGE, forge.game.keyword.Keyword.DREDGE,
            forge.game.keyword.Keyword.HARMONIZE);

    /**
     * Hints for graveyard decks (Anhelo, ...), read from Forge's card scripts:
     *   graveyard_use  how the card works from the graveyard: "landfall" (comes back
     *                  when you play a land, like Bloodghast), "trigger", "keyword"
     *                  (flashback, escape, madness...) or "ability"
     *   sac_me / discard_me   Forge's own "good to sacrifice / discard" rating (1-6)
     *   grants         keywords this permanent gives other cards, e.g. ["Casualty"]
     */
    private static void addGraveyardHints(JsonObject cj, Card c) {
        try {
            String use = null;
            for (forge.game.trigger.Trigger t : c.getTriggers()) {
                String zones = t.hasParam("TriggerZones") ? t.getParam("TriggerZones") : "";
                if (zones.contains("Graveyard")) {
                    boolean landfall = "Battlefield".equals(t.getParam("Destination"))
                            && t.hasParam("ValidCard") && t.getParam("ValidCard").startsWith("Land");
                    use = landfall ? "landfall" : (use == null ? "trigger" : use);
                }
            }
            if (use == null) {
                for (forge.game.keyword.KeywordInterface k : c.getKeywords()) {
                    if (GRAVEYARD_KEYWORDS.contains(k.getKeyword())) {
                        use = "keyword";
                        break;
                    }
                }
            }
            if (use == null) {
                for (forge.game.spellability.SpellAbility sa : c.getSpellAbilities()) {
                    if (sa.getRestrictions().getZone() == ZoneType.Graveyard) {
                        use = "ability";
                        break;
                    }
                }
            }
            if (use != null) {
                cj.addProperty("graveyard_use", use);
            }
            for (String svar : new String[] {"SacMe", "DiscardMe"}) {
                if (c.hasSVar(svar)) {
                    String key = svar.equals("SacMe") ? "sac_me" : "discard_me";
                    try {
                        cj.addProperty(key, Integer.parseInt(c.getSVar(svar).trim()));
                    } catch (NumberFormatException e) {
                        cj.addProperty(key, 1);
                    }
                }
            }
            if (c.isInZone(ZoneType.Battlefield) || c.isInZone(ZoneType.Command)) {
                JsonArray grants = new JsonArray();
                for (forge.game.staticability.StaticAbility st : c.getStaticAbilities()) {
                    if (st.hasParam("AddKeyword") && !"Self".equals(st.getParam("AffectedDefined"))) {
                        for (String kw : st.getParam("AddKeyword").split(" & ")) {
                            grants.add(kw.split(":")[0].trim()); // "Casualty:2" -> "Casualty"
                        }
                    }
                }
                if (!grants.isEmpty()) {
                    cj.add("grants", grants);
                }
            }
        } catch (RuntimeException e) {
            // hints are optional; never break a snapshot over them
        }
    }

    public static JsonObject card(Card c) {
        JsonObject cj = new JsonObject();
        cj.addProperty("id", c.getId());
        cj.addProperty("name", c.getName());
        cj.addProperty("cmc", c.getCMC());
        cj.addProperty("type", c.getType().toString());
        cj.addProperty("tapped", c.isTapped());
        if (c.isCreature()) {
            cj.addProperty("power", c.getNetPower());
            cj.addProperty("toughness", c.getNetToughness());
            cj.addProperty("sick", c.isSick());
        }
        if (c.getController() != null) {
            cj.addProperty("controller", c.getController().getName());
        }
        if (c.getOwner() != null) {
            cj.addProperty("owner", c.getOwner().getName()); // differs from controller after a steal
        }
        if (c.isToken()) {
            cj.addProperty("token", true);
            // Enough to pick the right token picture: many tokens share a name
            // ("Knight" 2/2 with vigilance vs. with haste and protection).
            forge.card.ColorSet colors = c.getColor();
            StringBuilder wubrg = new StringBuilder();
            if (colors.hasWhite()) wubrg.append('W');
            if (colors.hasBlue()) wubrg.append('U');
            if (colors.hasBlack()) wubrg.append('B');
            if (colors.hasRed()) wubrg.append('R');
            if (colors.hasGreen()) wubrg.append('G');
            cj.addProperty("colors", wubrg.toString());
            cj.addProperty("oracle", c.getOracleText());
            if (c.isCreature()) {
                cj.addProperty("base_power", c.getBasePower());
                cj.addProperty("base_toughness", c.getBaseToughness());
            }
        }
        addGraveyardHints(cj, c);
        if (c.isCommander()) {
            cj.addProperty("commander", true);
            if (c.getOwner() != null) {
                // Commander tax: {2} more for each time it was already cast from the command zone.
                cj.addProperty("commander_tax", 2 * c.getOwner().getCommanderCast(c));
            }
        }
        if (c.isFaceDown()) {
            cj.addProperty("face_down", true);
        }
        Card attachedTo = c.getAttachedTo();
        if (attachedTo != null) {
            cj.addProperty("attached_to", attachedTo.getId()); // auras and equipment
        }
        Combat combat = c.getGame() == null ? null : c.getGame().getPhaseHandler().getCombat();
        if (combat != null && c.isCreature()) {
            if (combat.isAttacking(c)) {
                cj.addProperty("attacking", combat.getDefenderByAttacker(c) == null
                        ? "?" : combat.getDefenderByAttacker(c).getName());
            } else if (combat.isBlocking(c)) {
                cj.addProperty("blocking", true);
                JsonArray blocks = new JsonArray(); // ids of the attackers it blocks
                for (Card attacker : combat.getAttackersBlockedBy(c)) {
                    blocks.add(attacker.getId());
                }
                cj.add("blocks", blocks);
            }
        }
        JsonObject chosen = chosen(c);
        if (chosen.size() > 0) {
            cj.add("chosen", chosen); // e.g. {"color": "Blue"} for a Thriving land
        }
        if (!c.getCounters().isEmpty()) {
            JsonObject counters = new JsonObject();
            for (CounterType type : c.getCounters().elementSet()) {
                counters.addProperty(type.getName(), c.getCounters().count(type));
            }
            cj.add("counters", counters); // e.g. {"+1/+1": 2, "Loyalty": 4}
        }
        return cj;
    }

    /**
     * Choices made for a card as it entered or resolved: "choose a color"
     * (Thriving lands), "choose a creature type", "choose a player", "name a
     * card", "choose a number". Empty when nothing was chosen.
     */
    public static JsonObject chosen(Card c) {
        JsonObject ch = new JsonObject();
        if (c.hasChosenColor()) {
            ch.addProperty("color", String.join(", ", c.getChosenColors()));
        }
        if (c.hasChosenType()) {
            ch.addProperty("type", c.getChosenType());
        }
        if (c.hasChosenPlayer()) {
            ch.addProperty("player", c.getChosenPlayer().getName());
        }
        if (c.hasNamedCard()) {
            ch.addProperty("name", c.getNamedCard());
        }
        if (c.hasChosenNumber()) {
            ch.addProperty("number", c.getChosenNumber());
        }
        return ch;
    }

    /** Spells and abilities waiting to resolve, top of the stack first. */
    public static JsonArray stack(Game game) {
        JsonArray arr = new JsonArray();
        for (SpellAbilityStackInstance si : game.getStack()) {
            JsonObject sj = new JsonObject();
            sj.addProperty("id", si.getId());
            sj.addProperty("description", si.getStackDescription());
            // "spell" = a card being cast; otherwise an activated or triggered ability.
            sj.addProperty("kind", si.isSpell() ? "spell" : si.isTrigger() ? "trigger" : "ability");
            sj.add("targets", targets(game, si));
            if (si.getSourceCard() != null) {
                sj.add("card", card(si.getSourceCard()));
            }
            if (si.getActivatingPlayer() != null) {
                sj.addProperty("controller", si.getActivatingPlayer().getName());
            }
            arr.add(sj);
        }
        return arr;
    }

    /**
     * What a stack item targets, as a list of {"card": id}, {"player": name} or
     * {"spell": stack id}. Includes the targets of its parts ("sub abilities"),
     * e.g. a spell that deals damage to one creature and then another.
     */
    static JsonArray targets(Game game, SpellAbilityStackInstance si) {
        JsonArray out = new JsonArray();
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (SpellAbilityStackInstance part = si; part != null; part = part.getSubInstance()) {
            TargetChoices choices = part.getTargetChoices();
            if (choices == null) {
                continue;
            }
            for (GameObject o : choices) {
                JsonObject t = new JsonObject();
                if (o instanceof Card c) {
                    t.addProperty("card", c.getId());
                } else if (o instanceof Player p) {
                    t.addProperty("player", p.getName());
                } else if (o instanceof SpellAbility sa) {
                    SpellAbilityStackInstance target = game.getStack().getInstanceMatchingSpellAbilityID(sa);
                    if (target == null) {
                        continue;
                    }
                    t.addProperty("spell", target.getId());
                } else {
                    continue;
                }
                if (seen.add(t.toString())) {
                    out.add(t);
                }
            }
        }
        return out;
    }

    /** Game log lines added after the first {@code alreadySent} ones, oldest first. */
    public static JsonArray logLines(Game game, int alreadySent) {
        List<GameLogEntry> entries = game.getGameLog().getAllEntries();
        JsonArray lines = new JsonArray();
        for (int i = Math.max(0, alreadySent); i < entries.size(); i++) {
            lines.add(entries.get(i).toString());
        }
        return lines;
    }

    /** Like {@link #logLines} but with each line's category, e.g. {"type": "LAND", "text": "..."}. */
    public static JsonArray logEntries(Game game, int alreadySent) {
        List<GameLogEntry> entries = game.getGameLog().getAllEntries();
        JsonArray out = new JsonArray();
        for (int i = Math.max(0, alreadySent); i < entries.size(); i++) {
            JsonObject e = new JsonObject();
            e.addProperty("type", entries.get(i).type().name());
            e.addProperty("text", entries.get(i).message()); // without the "Land: " prefix
            out.add(e);
        }
        return out;
    }
}
