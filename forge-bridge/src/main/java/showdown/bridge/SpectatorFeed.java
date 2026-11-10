package showdown.bridge;

import com.google.common.eventbus.Subscribe;

import forge.game.Game;
import forge.game.event.GameEvent;
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
        boolean calmMoment = event instanceof GameEventTurnPhase
                || event instanceof GameEventPlayerPriority
                || event instanceof GameEventCombatChanged;
        log.flush(game, bridge, calmMoment);
    }
}
