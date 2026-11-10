package showdown.bridge;

import forge.ai.LobbyPlayerAi;
import forge.game.Game;
import forge.game.player.Player;

/**
 * A seat at the table that Forge fills with a {@link PythonPlayerController}.
 *
 * Forge separates the "lobby player" (who sits down before the game) from the
 * in-game Player object. When the game starts, Forge asks each lobby player to
 * create its in-game Player; that is where we plug in our controller.
 *
 * We extend Forge's AI lobby player so that the AI profile (the settings the
 * fallback AI uses for decisions Python doesn't make) is set up as usual.
 */
public class PythonLobbyPlayer extends LobbyPlayerAi {
    private final BridgeConnection bridge;
    private final PythonPlayerController.LogCursor log;

    public PythonLobbyPlayer(String name, BridgeConnection bridge, PythonPlayerController.LogCursor log) {
        super(name, null);
        this.bridge = bridge;
        this.log = log;
        setAiProfile("Default");
    }

    @Override
    public Player createIngamePlayer(Game game, int id) {
        Player player = new Player(getName(), game, id);
        player.setFirstController(new PythonPlayerController(game, player, this, bridge, log));
        return player;
    }
}
