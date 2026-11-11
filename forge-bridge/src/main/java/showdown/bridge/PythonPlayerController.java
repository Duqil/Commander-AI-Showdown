package showdown.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import forge.LobbyPlayer;
import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCost;
import forge.ai.PlayerControllerAi;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * A Forge player whose decisions are made in Python.
 *
 * Forge asks a "PlayerController" every time a player has to decide
 * something. There are about 150 different questions (pick targets, order
 * blockers, choose a mode, scry, ...). Answering all of them from Python on
 * day one would be a huge job, so we take a shortcut:
 *
 *   We EXTEND Forge's own AI controller (PlayerControllerAi) and override only
 *   the decisions we want Python to make. Every other question still gets
 *   answered by Forge's AI.
 *
 * Decisions Python makes today:
 *   1. mulligan       - keep or mulligan the opening hand
 *   2. choose_play    - with priority: which spell/ability/land to play, or pass
 *      choose_targets - the targets of the play Python just chose
 *   3. attack         - which creatures attack which player
 *
 * Other details of a chosen play (X, modes, how to pay mana) and the targets
 * of triggered abilities are still filled in by Forge's AI. Moving more decisions to Python later means overriding more
 * methods here, one at a time.
 */
public class PythonPlayerController extends PlayerControllerAi {
    private final BridgeConnection bridge;
    private final LogCursor log;

    /** Protects against a play that Forge keeps refusing (e.g. no legal target). */
    private String lastPriorityKey = "";
    private int asksAtSamePriority = 0;
    private static final int MAX_ASKS_AT_SAME_PRIORITY = 15;

    public PythonPlayerController(Game game, Player p, LobbyPlayer lp, BridgeConnection bridge, LogCursor log) {
        super(game, p, lp);
        this.bridge = bridge;
        this.log = log;
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    /** Build the common part of a decision message and send it to Python. */
    private JsonObject ask(String kind, JsonObject extra) {
        log.flush(getGame(), bridge);
        JsonObject msg = extra == null ? new JsonObject() : extra;
        msg.addProperty("kind", kind);
        msg.addProperty("player", getPlayer().getName());
        msg.add("state", StateSnapshot.of(getGame(), getPlayer()));
        return bridge.ask(msg);
    }

    private static boolean isNull(JsonElement e) {
        return e == null || e.isJsonNull();
    }

    // ------------------------------------------------------------------
    // 1. Mulligan
    // ------------------------------------------------------------------

    @Override
    public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
        JsonObject extra = new JsonObject();
        extra.addProperty("cards_to_bottom_if_kept", cardsToReturn);
        extra.addProperty("going_first", firstPlayer == getPlayer());
        JsonObject reply = ask("mulligan", extra);
        JsonElement choice = reply.get("choice");
        if (isNull(choice)) {
            return super.mulliganKeepHand(firstPlayer, cardsToReturn); // Python said "you decide"
        }
        return choice.getAsBoolean(); // true = keep
    }

    // ------------------------------------------------------------------
    // 2. What to do with priority
    // ------------------------------------------------------------------

    /** Every spell, ability and land drop this player could legally start right now. */
    private List<SpellAbility> legalPlays() {
        Player me = getPlayer();
        CardCollection available = ComputerUtilAbility.getAvailableCards(getGame(), me);
        List<SpellAbility> result = new ArrayList<>();
        for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(available, me)) {
            try {
                if (sa.isManaAbility()) {
                    continue; // mana abilities are used while paying, not "played"
                }
                sa.setActivatingPlayer(me);
                if (!sa.canPlay()) {
                    continue;
                }
                if (!sa.isLandAbility() && !ComputerUtilCost.canPayCost(sa, me, false)) {
                    continue;
                }
                if (getAi().hasFailedToCast(sa)) {
                    continue; // Forge already tried this one this turn and it didn't work
                }
                result.add(sa);
            } catch (RuntimeException e) {
                // A few odd cards throw while being checked; just leave them out.
            }
        }
        return result;
    }

    @Override
    public List<SpellAbility> chooseSpellAbilityToPlay() {
        List<SpellAbility> plays = legalPlays();
        if (plays.isEmpty()) {
            return null; // nothing to do: pass priority without bothering Python
        }

        // Loop guard: if we keep being asked in the exact same spot, a play is
        // failing silently. Pass instead of looping forever.
        Game g = getGame();
        String key = g.getPhaseHandler().getTurn() + "|" + g.getPhaseHandler().getPhase() + "|"
                + g.getStack().size() + "|" + g.getGameLog().size();
        asksAtSamePriority = key.equals(lastPriorityKey) ? asksAtSamePriority + 1 : 0;
        lastPriorityKey = key;
        if (asksAtSamePriority >= MAX_ASKS_AT_SAME_PRIORITY) {
            return null;
        }

        JsonArray options = new JsonArray();
        for (int i = 0; i < plays.size(); i++) {
            SpellAbility sa = plays.get(i);
            JsonObject o = new JsonObject();
            o.addProperty("index", i);
            o.add("card", StateSnapshot.card(sa.getHostCard()));
            o.addProperty("description", sa.toString());
            o.addProperty("is_land", sa.isLandAbility());
            o.addProperty("is_spell", sa.isSpell());
            options.add(o);
        }
        JsonObject extra = new JsonObject();
        extra.add("options", options);

        JsonElement choice = ask("choose_play", extra).get("choice");
        if (isNull(choice)) {
            return null; // pass priority
        }
        int index = choice.getAsInt();
        if (index < 0 || index >= plays.size()) {
            System.err.println("[bridge] Python chose option " + index + " which does not exist; passing.");
            return null;
        }
        SpellAbility sa = plays.get(index);
        if (usesTargeting(sa) && !chooseTargets(sa)) {
            return null; // no legal targets after all: pass instead of a cast that fails
        }
        List<SpellAbility> chosen = new ArrayList<>();
        chosen.add(sa);
        return chosen; // Forge then calls playChosenSpellAbility(), which the AI handles
    }

    // ------------------------------------------------------------------
    // 2b. Targets for the play Python just chose
    // ------------------------------------------------------------------

    /** Whether the ability or any of its sub-abilities ("then ...") has targets. */
    private static boolean usesTargeting(SpellAbility sa) {
        for (SpellAbility a = sa; a != null; a = a.getSubAbility()) {
            if (a.usesTargeting()) {
                return true;
            }
        }
        return false;
    }

    /**
     * Fill in the targets of a play Python chose.
     *
     * First Forge's AI picks targets the way it would for itself; that is
     * sent to Python as "suggested". Then Python is asked for each part of the
     * ability that targets: it can keep the suggestion (answer null) or pick
     * other targets from the list of legal ones.
     *
     * @return false if the play can't get legal targets, so it shouldn't be cast
     */
    private boolean chooseTargets(SpellAbility sa) {
        for (SpellAbility a = sa; a != null; a = a.getSubAbility()) {
            a.resetTargets();
        }
        try {
            getAi().canPlaySa(sa); // the AI's check fills in the targets it likes (its verdict is ignored)
        } catch (RuntimeException e) {
            // a few cards throw here; Python can still choose
        }

        for (SpellAbility a = sa; a != null; a = a.getSubAbility()) {
            if (!a.usesTargeting()) {
                continue;
            }
            List<GameEntity> candidates = new ArrayList<>(a.getTargetRestrictions().getAllCandidates(a));
            JsonArray candJson = new JsonArray();
            JsonArray suggested = new JsonArray();
            for (int i = 0; i < candidates.size(); i++) {
                GameEntity e = candidates.get(i);
                JsonObject cj;
                if (e instanceof Card c) {
                    cj = StateSnapshot.card(c);
                    cj.addProperty("is_player", false);
                } else {
                    Player p = (Player) e;
                    cj = new JsonObject();
                    cj.addProperty("name", p.getName());
                    cj.addProperty("life", p.getLife());
                    cj.addProperty("is_player", true);
                }
                cj.addProperty("index", i);
                candJson.add(cj);
                if (a.getTargets().contains(e)) {
                    suggested.add(i);
                }
            }
            JsonObject extra = new JsonObject();
            extra.add("source", StateSnapshot.card(sa.getHostCard()));
            extra.addProperty("description", a.getDescription());
            extra.addProperty("min", a.getMinTargets());
            extra.addProperty("max", Math.min(a.getMaxTargets(), candidates.size()));
            extra.add("candidates", candJson);
            extra.add("suggested", suggested); // what Forge's AI would pick
            JsonElement choice = ask("choose_targets", extra).get("choice");

            if (!isNull(choice)) {
                a.resetTargets();
                for (JsonElement idx : choice.getAsJsonArray()) {
                    int i = idx.getAsInt();
                    if (i >= 0 && i < candidates.size() && a.canTarget(candidates.get(i))
                            && !a.getTargets().contains(candidates.get(i)) && a.canAddMoreTarget()) {
                        a.getTargets().add(candidates.get(i));
                    } else {
                        System.err.println("[bridge] Ignoring illegal target " + i + " for " + a);
                    }
                }
            }
            if (!a.isTargetNumberValid() || (a.getMinTargets() > 0 && a.getTargets().isEmpty())) {
                return false;
            }
        }
        return getGame().getStack().hasLegalTargeting(sa);
    }

    // ------------------------------------------------------------------
    // 3. Attacks
    // ------------------------------------------------------------------

    @Override
    public void declareAttackers(Player attacker, Combat combat) {
        List<Card> possible = new ArrayList<>();
        for (Card c : attacker.getCreaturesInPlay()) {
            if (CombatUtil.canAttack(c)) {
                possible.add(c);
            }
        }
        if (possible.isEmpty()) {
            return; // nobody can attack, no need to ask
        }

        // Who can be attacked: opponents, their planeswalkers and battles.
        List<GameEntity> defenders = new ArrayList<>(combat.getDefenders());
        JsonArray defendersJson = new JsonArray();
        for (int i = 0; i < defenders.size(); i++) {
            GameEntity d = defenders.get(i);
            JsonObject dj = new JsonObject();
            dj.addProperty("index", i);
            dj.addProperty("name", d.getName());
            dj.addProperty("is_player", d instanceof Player);
            defendersJson.add(dj);
        }

        JsonArray attackersJson = new JsonArray();
        for (Card c : possible) {
            JsonObject aj = StateSnapshot.card(c);
            JsonArray legal = new JsonArray();
            for (int i = 0; i < defenders.size(); i++) {
                if (CombatUtil.canAttack(c, defenders.get(i))) {
                    legal.add(i);
                }
            }
            aj.add("can_attack", legal);
            attackersJson.add(aj);
        }

        JsonObject extra = new JsonObject();
        extra.add("attackers", attackersJson);
        extra.add("defenders", defendersJson);
        JsonElement choice = ask("attack", extra).get("choice");
        if (isNull(choice)) {
            super.declareAttackers(attacker, combat); // Python said "you decide"
            return;
        }

        // choice looks like {"<card id>": <defender index>, ...}
        Map<Integer, Card> byId = new HashMap<>();
        for (Card c : possible) {
            byId.put(c.getId(), c);
        }
        for (Map.Entry<String, JsonElement> e : choice.getAsJsonObject().entrySet()) {
            Card c = byId.get(Integer.parseInt(e.getKey()));
            int d = e.getValue().getAsInt();
            if (c == null || d < 0 || d >= defenders.size() || !CombatUtil.canAttack(c, defenders.get(d))) {
                System.err.println("[bridge] Ignoring illegal attack " + e.getKey() + " -> " + d);
                continue;
            }
            combat.addAttacker(c, defenders.get(d));
        }

        // Forge checks rules like "must attack" or "can't attack alone".
        // If Python's attack breaks one, fall back to Forge's AI for this combat.
        if (!CombatUtil.validateAttackers(combat)) {
            System.err.println("[bridge] Python's attack broke an attack rule; Forge AI attacks instead.");
            combat.clearAttackers();
            super.declareAttackers(attacker, combat);
        }
    }

    /**
     * Keeps track of how much of the game log Python has already seen, and
     * sends Python an "event" message whenever something new happened.
     *
     * Each event carries the new log lines and, for the browser viewer, a
     * spectator snapshot of the whole table. Identical snapshots are not
     * sent twice, which keeps saved games small.
     */
    public static final class LogCursor {
        private int sent = 0;
        private String lastState = "";

        /** Send new log lines (if any), plus the table state if it changed. */
        public synchronized void flush(Game game, BridgeConnection bridge) {
            flush(game, bridge, false);
        }

        /**
         * @param checkState also send an event when only the table changed
         *                   (no new log lines), e.g. at the start of a phase
         */
        public synchronized void flush(Game game, BridgeConnection bridge, boolean checkState) {
            int size = game.getGameLog().size();
            if (size < sent) {
                sent = size; // Forge undid something (AI rollback) and shortened the log
            }
            boolean newLines = size > sent;
            if (!newLines && !checkState) {
                return;
            }
            JsonObject state = StateSnapshot.spectator(game);
            String stateText = state.toString();
            boolean stateChanged = !stateText.equals(lastState);
            if (!newLines && !stateChanged) {
                return; // nothing new to tell
            }
            JsonObject event = new JsonObject();
            event.addProperty("type", "event");
            event.add("log", StateSnapshot.logLines(game, sent));       // plain text, as before
            event.add("entries", StateSnapshot.logEntries(game, sent)); // same lines with their category
            if (stateChanged) {
                event.add("state", state);
                lastState = stateText;
            }
            sent = size;
            bridge.send(event);
        }
    }
}
