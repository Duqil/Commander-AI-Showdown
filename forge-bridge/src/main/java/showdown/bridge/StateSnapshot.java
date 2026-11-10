package showdown.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.Game;
import forge.game.GameLogEntry;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

import java.util.List;

/**
 * Turns Forge's game objects into plain JSON that Python can read.
 *
 * A snapshot is taken from one player's point of view: you see your own hand,
 * but only the hand SIZE of your opponents. That matters for The Boys later:
 * a learning bot must not be allowed to peek at hidden cards.
 */
public final class StateSnapshot {
    private StateSnapshot() { }

    public static JsonObject of(Game game, Player viewer) {
        JsonObject state = new JsonObject();
        state.addProperty("turn", game.getPhaseHandler().getTurn());
        Player active = game.getPhaseHandler().getPlayerTurn();
        state.addProperty("active_player", active == null ? null : active.getName());
        var phase = game.getPhaseHandler().getPhase();
        state.addProperty("phase", phase == null ? null : phase.name()); // e.g. "MAIN1", "COMBAT_DECLARE_ATTACKERS"
        state.addProperty("you", viewer.getName());
        state.addProperty("stack_size", game.getStack().size());

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
            if (p == viewer) {
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
        return cj;
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
}
