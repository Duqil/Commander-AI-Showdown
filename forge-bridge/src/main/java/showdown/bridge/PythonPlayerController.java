package showdown.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import forge.LobbyPlayer;
import forge.ai.AiCache;
import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCost;
import forge.ai.ComputerUtilMana;
import forge.ai.PlayerControllerAi;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.player.DelayedReveal;
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
 *      keyword_cost   - pay an optional extra cost (casualty, offspring...) or not
 *   4. pick_card      - cards you pick for yourself: lands from a library search
 *                       (any fetch land), or "look at the top N" (Drawn from Dreams)
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
        GameLimits.beforeAsk(getGame()); // a game that never ends becomes a draw (GameLimits.java)
        try {
            return bridge.ask(msg);
        } finally {
            GameLimits.afterAsk();
        }
    }

    private static boolean isNull(JsonElement e) {
        return e == null || e.isJsonNull();
    }

    // ------------------------------------------------------------------
    // 1. Mulligan
    // ------------------------------------------------------------------

    /** Keep without asking once keeping would leave only 2 cards (7 - 5). */
    private static final int MAX_CARDS_TO_BOTTOM = 5;

    @Override
    public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
        if (cardsToReturn >= MAX_CARDS_TO_BOTTOM) {
            // Forge lets you mulligan forever. A bot that always answers "mulligan"
            // (a Boy that learned to dislike keeping) would hang the game, so at
            // 2 cards left we keep, whatever the bot thinks.
            return true;
        }
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
        CardCollection available = playableCards(me);
        List<SpellAbility> result = new ArrayList<>();
        // "As an additional cost, sacrifice an artifact or discard a card"
        // (Demand Answers): Forge lists such a spell once, without the extra
        // cost. Its own AI swaps in one version per way of paying, and so do
        // we; otherwise the plain version gets cast and nobody pays anything.
        // (Only that step: Forge's helper for it would also recheck every
        // alternative cost again, which is slow with Anhelo on the table.)
        List<SpellAbility> all = new ArrayList<>();
        for (SpellAbility sa : candidateAbilities(available, me)) {
            sa.setActivatingPlayer(me);
            all.addAll(forge.game.GameActionUtil.getAdditionalCostSpell(sa));
        }
        for (SpellAbility sa : all) {
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
                if (sa.costHasManaX() && maxX(sa) < 1) {
                    continue; // X would be 0: a Ravenous 0/0 just dies, an X burn spell does nothing
                }
                if (lacksTargets(sa)) {
                    continue; // "destroy target artifact" with no artifact around: nothing would happen
                }
                result.add(sa);
            } catch (RuntimeException e) {
                // A few odd cards throw while being checked; just leave them out.
            }
        }
        return result;
    }

    /**
     * The cards worth checking for plays: Forge's list, minus cards in exile,
     * graveyards or on top of a library that this player can't play anyway.
     *
     * Why: checking one card can be slow (Forge re-runs every static ability
     * for it when something changes spells on the stack). Forge's list has
     * EVERY exiled card at the table, and in long games exile can hold dozens
     * of cards (Virtue of Courage exiles a lot), which made one game take 45
     * minutes. Leaving out cards nobody may cast keeps every legal play.
     */
    /**
     * The spells and abilities of these cards, before checking if they can be
     * paid for or are allowed right now.
     *
     * Why not just ask Forge for all of them? For every card it also works out
     * "could this be cast in some other way?" (alternative costs). When a
     * permanent gives spells a keyword on the stack (Anhelo gives Casualty),
     * that question makes Forge recheck every static ability on the table,
     * twice, for every spell-shaped thing it looks at. A permanent on the
     * battlefield still carries its own "cast me" spell, and a land its own
     * "play me" land drop. Forge runs the slow question for those land drops,
     * so a board with 40 lands meant 80 full rechecks at every priority stop,
     * for land drops that can't happen from the battlefield anyway.
     *
     * So for ordinary face-up permanents we take their abilities directly and
     * skip their spells and land drops. Unusual permanents (face-down, Rooms, phased out) and
     * every card outside the battlefield still go the full Forge way, so
     * alternative costs like Gut Shot's "pay 2 life" are still found.
     */
    private static List<SpellAbility> candidateAbilities(CardCollection cards, Player me) {
        CardCollection fullCheck = new CardCollection();
        List<SpellAbility> result = new ArrayList<>();
        for (Card c : cards) {
            if (!c.isInPlay() || c.isFaceDown() || c.isPhasedOut() || isRoomState(c)) {
                fullCheck.add(c);
                continue;
            }
            for (SpellAbility sa : c.getSpellAbilities()) {
                if (sa.isSpell() || sa.isLandAbility()) {
                    continue; // "cast me" / "play me as my land drop": not from here
                }
                result.add(sa);
                // Only cheap non-spell extras here (e.g. Piracy mana).
                result.addAll(forge.game.GameActionUtil.getAlternativeCosts(sa, me, false));
            }
        }
        result.addAll(ComputerUtilAbility.getSpellAbilities(fullCheck, me));
        return result;
    }

    /** Rooms (split-door enchantments) get unlock abilities Forge adds separately. */
    private static boolean isRoomState(Card c) {
        forge.card.CardStateName s = c.getCurrentStateName();
        return s == forge.card.CardStateName.LeftSplit || s == forge.card.CardStateName.RightSplit
                || s == forge.card.CardStateName.EmptyRoom;
    }

    private CardCollection playableCards(Player me) {
        CardCollection result = new CardCollection();
        boolean sorcerySpeed = me.canCastSorcery(); // my main phase, empty stack
        for (Card c : ComputerUtilAbility.getAvailableCards(getGame(), me)) {
            ZoneType zone = c.getZone() == null ? null : c.getZone().getZoneType();
            boolean hidden = zone == ZoneType.Exile || zone == ZoneType.Library || zone == ZoneType.Graveyard;
            if (hidden && !mayCastFrom(c, me, zone)) {
                continue;
            }
            if (!sorcerySpeed && zone != ZoneType.Battlefield && !usableAtInstantSpeed(c, me)) {
                continue;
            }
            result.add(c);
        }
        return result;
    }

    /**
     * Outside my own main phase, could anything on this card (in hand, the
     * command zone, exile...) be used right now?
     *
     * Why: checking a card is slow when something changes spells on the stack
     * (Anhelo gives spells casualty: Forge then re-runs every static ability
     * for each card). We check every player at every priority stop, so on big
     * tables games took minutes. A creature or sorcery in hand can't be cast
     * on someone else's turn anyway, so we skip it there.
     *
     * Kept: instants, flash, cards given flash by another card, and abilities
     * that work from the hand (cycling, ninjutsu, channel...). Mana isn't
     * checked here, so a spell you can pay for with life still counts.
     * Abilities of permanents on the battlefield are always checked.
     */
    private static boolean usableAtInstantSpeed(Card c, Player me) {
        for (SpellAbility sa : c.getAllSpellAbilities()) { // every face: adventures, split and MDFC cards
            if (sa.isManaAbility()) {
                continue;
            }
            if (!sa.isSpell() && !sa.isLandAbility()) {
                return true; // an ability (cycling, ninjutsu...): Forge checks its timing later
            }
            if (sa.isSpell() && sa.withFlash(c, me)) {
                return true;
            }
        }
        return false;
    }

    /** Could this player cast or play this card from its graveyard, exile or library? */
    private static boolean mayCastFrom(Card c, Player me, ZoneType zone) {
        if (!c.mayPlay(me).isEmpty()) {
            return true; // "you may play/cast it" effects: Virtue of Courage, adventures, impulse draw...
        }
        if (!me.equals(c.getOwner())) {
            return false;
        }
        if (c.isForetold() || c.isPlotted()) {
            return true;
        }
        for (SpellAbility sa : c.getSpellAbilities()) {
            if (sa.getRestrictions().getZone() == zone) {
                return true; // the card itself says it can be used from there (Unearth, ...)
            }
        }
        // Flashback, Escape, Retrace... are keywords, also when another card grants them.
        return zone == ZoneType.Graveyard && !c.getKeywords().isEmpty();
    }

    @Override
    public List<SpellAbility> chooseSpellAbilityToPlay() {
        // Forge's AI keeps a shared cache of answers (AiCache) and empties it
        // each time *its* players choose a play. With Python choosing instead,
        // nobody emptied it: it grew all game long, held on to every finished
        // game, slowed down lookups (they scan the whole list) and in the end
        // ran Java out of memory, which killed the game. Empty it here, the
        // same spot Forge's AI does.
        AiCache.clear();
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

        // Forge's AI also judges each play (forgeWouldPlay). For a modal spell
        // (Charm: "choose one or more", Spree, Escalate) that check is also
        // where the modes get picked, and Forge's AI picks them again at cast
        // time. If it finds no mode worth choosing, the spell would resolve
        // doing nothing (Collective Resistance cast with no mode), so it isn't
        // offered at all.
        List<SpellAbility> offered = new ArrayList<>();
        List<Boolean> forgeOk = new ArrayList<>();
        for (SpellAbility sa : plays) {
            boolean ok = forgeWouldPlay(sa);
            if (isModal(sa) && (sa.getChosenList() == null || sa.getChosenList().isEmpty())) {
                continue;
            }
            offered.add(sa);
            forgeOk.add(ok);
        }
        plays = offered;
        if (plays.isEmpty()) {
            return null;
        }

        JsonArray options = new JsonArray();
        for (int i = 0; i < plays.size(); i++) {
            SpellAbility sa = plays.get(i);
            JsonObject o = new JsonObject();
            o.addProperty("index", i);
            JsonObject card = StateSnapshot.card(sa.getHostCard());
            card.addProperty("oracle", sa.getHostCard().getOracleText()); // rules text, for features like "counter booster"
            o.add("card", card);
            o.addProperty("description", sa.toString());
            o.addProperty("is_land", sa.isLandAbility());
            o.addProperty("is_spell", sa.isSpell());
            o.addProperty("is_ability", !sa.isSpell() && !sa.isLandAbility());
            if (sa.getApi() != null) {
                o.addProperty("api", sa.getApi().name()); // e.g. "ChangeZone" for Evolving Wilds
            }
            o.addProperty("forge_would_play", forgeOk.get(i));
            if (sa.isSpell() && sa.getHostCard().isPermanent() && etbHasNoTarget(sa)) {
                // A creature whose "when this enters, destroy target ..." has
                // nothing to hit right now: it would only be its body.
                o.addProperty("etb_no_target", true);
            }
            if (sa.getHostCard().getZone() != null) {
                // Where the card is: "Hand", "Exile" (impulse draw: playable for a short time), "Graveyard"...
                o.addProperty("zone", sa.getHostCard().getZone().getZoneType().name());
            }
            if (sa.costHasManaX()) {
                o.addProperty("max_x", maxX(sa)); // the biggest X we could pay right now
            }
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
        if (sa.costHasManaX() && !chooseX(sa)) {
            return null; // can't pay any X after all
        }
        List<SpellAbility> chosen = new ArrayList<>();
        chosen.add(sa);
        return chosen; // Forge then calls playChosenSpellAbility(), which the AI handles
    }

    /** The biggest X this player could pay for the ability right now (0 if none). */
    private int maxX(SpellAbility sa) {
        Integer old = sa.getXManaCostPaid();
        try {
            return ComputerUtilMana.determineLeftoverMana(sa, getPlayer(), false);
        } catch (RuntimeException e) {
            return 0;
        } finally {
            sa.setXManaCostPaid(old); // the check may change X; put it back
        }
    }

    /**
     * Pick X for an X spell Python chose.
     *
     * Forge's AI only picks X while it is deciding to make a play itself.
     * When Python picked the play instead, X was never set and the spell was
     * cast with X = 0 (a Ravenous creature entered as a 0/0 and died).
     *
     * Now Forge's AI picks X first, the way it would for itself. That matters:
     * for Starstorm (X damage to every creature) it tries every X and keeps
     * the one that kills the most enemy creatures for the fewest of ours,
     * which is often not the biggest X. Targeted spells already got Forge's X
     * together with the targets.
     *
     * If Forge's AI wouldn't cast it at any X: a creature or other permanent
     * (Tervigon, Hydroid Krasis...) still gets the biggest X we can pay,
     * because bigger is simply better there. Anything else isn't cast:
     * Starstorm with no good X would only hurt us.
     *
     * @return false if the spell shouldn't be cast
     */
    private boolean chooseX(SpellAbility sa) {
        Integer chosen = sa.getXManaCostPaid();
        if (!usesTargeting(sa)) {
            sa.setXManaCostPaid(null);
            try {
                getAi().canPlaySa(sa); // sets X the way Forge's AI likes it
            } catch (RuntimeException e) {
                // a few cards throw here; fall back below
            }
            chosen = sa.getXManaCostPaid();
        }
        if (chosen == null || chosen < 1) {
            chosen = sa.isSpell() && sa.getHostCard().isPermanent() ? maxX(sa) : 0;
        }
        if (chosen < 1) {
            sa.setXManaCostPaid(null);
            return false;
        }
        sa.setXManaCostPaid(Math.min(chosen, maxX(sa)));
        return true;
    }

    /**
     * Would Forge's own AI make this play right now? A hint for Python: it
     * knows things like "fetch a land with Evolving Wilds" or "don't pump a
     * creature that isn't in combat". Land drops always count as yes.
     */
    private boolean forgeWouldPlay(SpellAbility sa) {
        if (sa.isLandAbility()) {
            return true;
        }
        Integer oldX = sa.getXManaCostPaid();
        try {
            return getAi().canPlaySa(sa).willingToPlay();
        } catch (RuntimeException e) {
            return false; // a few cards throw here; call it a no
        } finally {
            sa.setXManaCostPaid(oldX); // the check picks an X as a side effect too
            for (SpellAbility a = sa; a != null; a = a.getSubAbility()) {
                a.resetTargets(); // the check picks targets as a side effect; forget them
            }
        }
    }

    // ------------------------------------------------------------------
    // 2b. Targets for the play Python just chose
    // ------------------------------------------------------------------

    /** A "choose one or more" spell or ability (Charm, Spree, Escalate...). */
    private static boolean isModal(SpellAbility sa) {
        return sa.getApi() == forge.game.ability.ApiType.Charm && !sa.isTrigger();
    }

    /**
     * Does some part of this play need a target that doesn't exist right now?
     * Then casting it would do nothing (or fail), so it isn't offered.
     * Modal spells pick their targets per mode later: see isModal.
     */
    private static boolean lacksTargets(SpellAbility sa) {
        try {
            for (SpellAbility a = sa; a != null; a = a.getSubAbility()) {
                if (a.usesTargeting() && a.getMinTargets() > 0
                        && a.getTargetRestrictions().getAllCandidates(a).isEmpty()) {
                    return true;
                }
            }
        } catch (RuntimeException e) {
            // odd cards: assume it has targets
        }
        return false;
    }

    /**
     * A permanent spell whose "when this enters" trigger needs a target, and
     * there is none right now (Witch Enchanter with no opposing artifact or
     * enchantment). The spell is still worth its body; Python decides.
     */
    private boolean etbHasNoTarget(SpellAbility sa) {
        try {
            for (forge.game.trigger.Trigger t : sa.getHostCard().getTriggers()) {
                if (t.getMode() != forge.game.trigger.TriggerType.ChangesZone
                        || !"Battlefield".equals(t.getParam("Destination"))
                        || !String.valueOf(t.getParam("ValidCard")).contains("Card.Self")) {
                    continue;
                }
                SpellAbility effect = t.ensureAbility();
                if (effect == null) {
                    continue;
                }
                effect.setActivatingPlayer(getPlayer());
                if (effect.usesTargeting()) {
                    return lacksTargets(effect);
                }
            }
        } catch (RuntimeException e) {
            // odd cards: say nothing
        }
        return false;
    }

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
            if (candidates.isEmpty() && a.getMinTargets() > 0) {
                return false; // nothing to target: no need to ask
            }
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
            // What the ability does, in Forge's own terms, e.g. "Destroy", "DealDamage", "Pump".
            extra.addProperty("api", a.getApi() == null ? null : a.getApi().name());
            JsonObject params = new JsonObject();
            for (String key : new String[] {"NumAtt", "NumDef", "Origin", "Destination", "CounterType", "ValidTgts"}) {
                if (a.hasParam(key)) {
                    params.addProperty(key, a.getParam(key)); // e.g. NumAtt "-2" = a shrink; ValidTgts "Creature.YouCtrl" = your own
                }
            }
            extra.add("params", params);
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

    // ------------------------------------------------------------------
    // 2a. Scry and surveil: Forge's AI decides, we write down what it did
    // ------------------------------------------------------------------

    /**
     * Forge's log only says "scry 2", not which cards went where. Add a
     * "Detail:" line to the game log (the viewer shows those in the
     * "Phases & mana" tab, not the main log), e.g.
     * "Detail: 1-Riku scry: Island on top, Shock to the bottom".
     */
    @Override
    public org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> arrangeForScry(CardCollection topN) {
        org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> result = super.arrangeForScry(topN);
        logDetail("scry", result.getLeft(), result.getRight(), "the bottom");
        return result;
    }

    @Override
    public org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> arrangeForSurveil(CardCollection topN) {
        org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> result = super.arrangeForSurveil(topN);
        logDetail("surveil", result.getLeft(), result.getRight(), "the graveyard");
        return result;
    }

    private void logDetail(String what, CardCollection top, CardCollection away, String awayName) {
        List<String> parts = new ArrayList<>();
        if (top != null && !top.isEmpty()) {
            parts.add(names(top) + " on top");
        }
        if (away != null && !away.isEmpty()) {
            parts.add(names(away) + " to " + awayName);
        }
        if (parts.isEmpty()) {
            return;
        }
        getGame().getGameLog().add(forge.game.GameLogEntryType.INFORMATION,
                "Detail: " + getPlayer().getName() + " " + what + ": " + String.join(", ", parts));
    }

    private static String names(CardCollection cards) {
        List<String> out = new ArrayList<>();
        for (Card c : cards) {
            out.add(c.getName());
        }
        return String.join(" and ", out);
    }

    // ------------------------------------------------------------------
    // 2b. Optional extra costs on a spell (casualty, offspring, ...)
    // ------------------------------------------------------------------

    /**
     * "As you cast this, you may pay an extra cost": casualty (sacrifice a
     * creature to copy the spell), offspring, conspire... Forge's AI pays
     * whenever it can, even if that means sacrificing its best creature.
     * Python gets to say no, or pay fewer times. It can't pay more than Forge
     * found payable. For sacrifice costs Python sees the creatures that could
     * be sacrificed (Forge picks its least valuable one when paying).
     */
    // Which spell's extra costs we're being asked about, which keywords of it
    // we've already seen, and for each sacrifice cost we agreed to pay, the
    // creatures that could pay it. See enoughCreaturesFor.
    private SpellAbility keywordCostSpell = null;
    private final java.util.Set<String> keywordCostsSeen = new java.util.HashSet<>();
    private final java.util.List<java.util.List<Card>> promisedSacrifices = new java.util.ArrayList<>();

    /**
     * Two casualties are fine (Flawless Forgery's own Casualty 3 plus Casualty 2
     * from Anhelo), but each needs its own creature. Forge's AI checks each
     * casualty on its own, so with a single big creature it said yes to both.
     * We only agree to another sacrifice cost when every sacrifice we've
     * agreed to for this cast can get a different creature.
     *
     * Forge asks about every keyword of the spell in a row, so seeing a new
     * spell, or the same keyword again, means a new cast.
     */
    private boolean enoughCreaturesFor(SpellAbility sa, forge.game.cost.Cost cost,
            forge.game.keyword.KeywordInterface keyword) {
        String key = keyword.getOriginal();
        if (sa != keywordCostSpell || keywordCostsSeen.contains(key)) {
            keywordCostSpell = sa;
            keywordCostsSeen.clear();
            promisedSacrifices.clear();
        }
        keywordCostsSeen.add(key);
        java.util.List<Card> candidates = sacrificeCandidates(sa, cost);
        if (candidates.isEmpty()) {
            return true; // no sacrifice in this cost
        }
        java.util.List<java.util.List<Card>> all = new java.util.ArrayList<>(promisedSacrifices);
        all.add(candidates);
        return distinctPicks(all, 0, new java.util.HashSet<>());
    }

    /** Can each list get its own card, no card used twice? (Tiny lists: just try.) */
    private static boolean distinctPicks(java.util.List<java.util.List<Card>> lists, int i, java.util.Set<Card> used) {
        if (i == lists.size()) {
            return true;
        }
        for (Card c : lists.get(i)) {
            if (used.add(c)) {
                if (distinctPicks(lists, i + 1, used)) {
                    return true;
                }
                used.remove(c);
            }
        }
        return false;
    }

    /** The creatures (or other permanents) that could pay this cost's sacrifice. */
    private java.util.List<Card> sacrificeCandidates(SpellAbility sa, forge.game.cost.Cost cost) {
        java.util.List<Card> result = new java.util.ArrayList<>();
        for (forge.game.cost.CostPart part : cost.getCostParts()) {
            if (part instanceof forge.game.cost.CostSacrifice sac) {
                result.addAll(forge.game.card.CardLists.getValidCards(getPlayer().getCardsIn(ZoneType.Battlefield),
                        sac.getType().split(";"), getPlayer(), sa.getHostCard(), sa));
            }
        }
        return result;
    }

    @Override
    public int chooseNumberForKeywordCost(SpellAbility sa, forge.game.cost.Cost cost,
            forge.game.keyword.KeywordInterface keyword, String prompt, int max) {
        if (!enoughCreaturesFor(sa, cost, keyword)) {
            return 0; // every creature that could pay is already promised to another casualty
        }
        int payable = chooseNumberForKeywordCostPython(sa, cost, keyword, prompt, max);
        java.util.List<Card> candidates = sacrificeCandidates(sa, cost);
        if (payable > 0 && !candidates.isEmpty()) {
            promisedSacrifices.add(candidates);
        }
        return payable;
    }

    private int chooseNumberForKeywordCostPython(SpellAbility sa, forge.game.cost.Cost cost,
            forge.game.keyword.KeywordInterface keyword, String prompt, int max) {
        int payable = super.chooseNumberForKeywordCost(sa, cost, keyword, prompt, max);
        if (payable < 1) {
            return payable; // nothing Python could choose
        }
        JsonObject extra = new JsonObject();
        extra.addProperty("keyword", keyword.getOriginal()); // e.g. "Casualty:2"
        extra.addProperty("cost", cost.toSimpleString());
        extra.addProperty("max", payable);
        extra.add("spell", StateSnapshot.card(sa.getHostCard()));
        JsonArray sacrifice = new JsonArray();
        for (Card c : sacrificeCandidates(sa, cost)) {
            sacrifice.add(StateSnapshot.card(c));
        }
        extra.add("sacrifice_candidates", sacrifice);
        JsonElement choice = ask("keyword_cost", extra).get("choice");
        if (isNull(choice)) {
            return payable;
        }
        return Math.max(0, Math.min(choice.getAsInt(), payable));
    }

    // ------------------------------------------------------------------
    // 4. Picking cards for yourself (land searches, "look at the top N")
    // ------------------------------------------------------------------

    /**
     * Searching your library for lands, with ANY card that does it (Evolving
     * Wilds, Terramorphic Expanse, Myriad Landscape, ...). Forge's AI only
     * fixes colors when it has no land in hand and can't cast anything, so a
     * three-color deck could fetch Island after Island. Cards that fetch
     * several lands ask once per land. Other library searches (tutors for
     * any card) still go to Forge's AI.
     */
    @Override
    public Card chooseSingleCardForZoneChange(ZoneType destination, List<ZoneType> origin, SpellAbility sa,
            CardCollection fetchList, DelayedReveal delayedReveal, String selectPrompt, boolean isOptional,
            Player decider) {
        Card suggestion = super.chooseSingleCardForZoneChange(destination, origin, sa, fetchList,
                delayedReveal, selectPrompt, isOptional, decider);
        boolean landSearch = origin != null && origin.contains(ZoneType.Library) && !fetchList.isEmpty()
                && fetchList.stream().allMatch(Card::isLand) && decider == getPlayer();
        if (!landSearch) {
            return suggestion;
        }
        return askPick(new ArrayList<>(fetchList), suggestion, sa, isOptional, "library_search");
    }

    /**
     * "Look at the top N cards, put some into your hand" (Drawn from Dreams,
     * Impulse, ...): every Dig effect that puts the cards you pick into your
     * hand or onto the battlefield. Python picks one card at a time.
     * Everything else that uses this question (sacrifice a creature, pick an
     * opponent's card, ...) still goes to Forge's AI.
     */
    @Override
    public <T extends GameEntity> T chooseSingleEntityForEffect(forge.util.collect.FCollectionView<T> optionList,
            DelayedReveal delayedReveal, SpellAbility sa, String title, boolean isOptional, Player targetedPlayer,
            Map<String, Object> params) {
        T suggestion = super.chooseSingleEntityForEffect(optionList, delayedReveal, sa, title, isOptional,
                targetedPlayer, params);
        if (!isDigForMe(sa, optionList)) {
            return suggestion;
        }
        List<Card> cards = new ArrayList<>();
        for (T e : optionList) {
            cards.add((Card) e);
        }
        Card picked = askPick(cards, (Card) suggestion, sa, isOptional, "look_at_top");
        @SuppressWarnings("unchecked")
        T result = (T) picked;
        return result;
    }

    private <T extends GameEntity> boolean isDigForMe(SpellAbility sa, Iterable<T> options) {
        if (sa == null || sa.getApi() != forge.game.ability.ApiType.Dig) {
            return false;
        }
        String dest = sa.hasParam("DestinationZone") ? sa.getParam("DestinationZone") : "Hand";
        if (!dest.equals("Hand") && !dest.equals("Battlefield")) {
            return false; // e.g. "put one on top of your library": not a pick for your hand
        }
        boolean any = false;
        for (T e : options) {
            if (!(e instanceof Card c) || c.getOwner() != getPlayer()) {
                return false;
            }
            any = true;
        }
        return any;
    }

    /** Ask Python to pick one of ``cards``. Falls back to Forge's suggestion. */
    private Card askPick(List<Card> cards, Card suggestion, SpellAbility sa, boolean isOptional, String how) {
        JsonArray candJson = new JsonArray();
        int suggested = -1;
        for (int i = 0; i < cards.size(); i++) {
            JsonObject cj = StateSnapshot.card(cards.get(i));
            cj.addProperty("index", i);
            candJson.add(cj);
            if (cards.get(i) == suggestion) {
                suggested = i;
            }
        }
        JsonObject extra = new JsonObject();
        extra.add("source", StateSnapshot.card(sa.getHostCard()));
        extra.addProperty("how", how);             // "library_search" or "look_at_top"
        extra.add("candidates", candJson);
        extra.addProperty("suggested", suggested); // what Forge's AI would pick (-1: nothing)
        extra.addProperty("optional", isOptional);
        JsonElement choice = ask("pick_card", extra).get("choice");
        if (isNull(choice)) {
            return suggestion;
        }
        int i = choice.getAsInt();
        if (i < 0) {
            return isOptional ? null : suggestion; // "take nothing" is only allowed when optional
        }
        if (i >= cards.size()) {
            System.err.println("[bridge] Ignoring illegal pick " + i);
            return suggestion;
        }
        return cards.get(i);
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
