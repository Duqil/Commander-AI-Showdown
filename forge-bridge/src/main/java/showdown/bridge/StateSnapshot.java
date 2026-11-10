package showdown.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.Game;
import forge.game.GameLogEntry;
import forge.game.card.Card;
import forge.game.card.CounterType;
import forge.game.combat.Combat;
import forge.game.player.Player;
import forge.game.spellability.SpellAbilityStackInstance;
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
        }
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
            sj.addProperty("description", si.getStackDescription());
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
