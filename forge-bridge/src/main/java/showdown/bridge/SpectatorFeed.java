package showdown.bridge;

import com.google.common.eventbus.Subscribe;

import forge.game.Game;
import forge.game.GameLogEntryType;
import forge.game.event.GameEvent;
import forge.game.event.GameEventCardSacrificed;
import forge.game.event.GameEventCombatChanged;
import forge.game.event.GameEventPlayerPriority;
import forge.game.event.GameEventTurnPhase;

/**
 * Streams the game to Python as it happens, for the browser viewer.
 *
 * Forge announces everything that happens ("a card was tapped", "a new phase
 * began", ...) on an event bus. This class listens to that bus. After each
 * announcement it forwards new log lines, and at a few calm moments (new
 * phase, someone gets priority, combat changed) it also sends a snapshot of
 * the whole table.
 *
 * Without this, Python only heard about the game when one of its own bots had
 * to decide something, so a seat played by Forge's AI was invisible.
 */
public final class SpectatorFeed {
    private final Game game;
    private final BridgeConnection bridge;
    private final PythonPlayerController.LogCursor log;

    public SpectatorFeed(Game game, BridgeConnection bridge, PythonPlayerController.LogCursor log) {
        this.game = game;
        this.bridge = bridge;
        this.log = log;
    }

    /** Guava's EventBus calls this for every event Forge fires, on the game thread. */
    @Subscribe
    public void onEvent(GameEvent event) {
        if (event instanceof GameEventCardSacrificed sacrificed
                && !sacrificed.card().getCurrentState().getType().isLand()) { // fetch lands already say what they do
            // Forge's log only says "X was put into Graveyard from Battlefield",
            // which looks the same as dying. Say it was a sacrifice (casualty,
            // Bloodghast fodder, ...), so the viewer shows what paid for what.
            var card = sacrificed.card();
            String who = card.getController() == null ? "Someone" : card.getController().getName();
            game.getGameLog().add(GameLogEntryType.ZONE_CHANGE, who + " sacrifices " + card.getName());
        }
        boolean calmMoment = event instanceof GameEventTurnPhase
                || event instanceof GameEventPlayerPriority
                || event instanceof GameEventCombatChanged;
        log.flush(game, bridge, calmMoment);
    }
}
