package showdown.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.GuiDesktop;
import forge.deck.Deck;
import forge.deck.io.DeckSerializer;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameOutcome;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.gui.GuiBase;
import forge.model.FModel;
import forge.player.GamePlayerUtil;
import forge.util.MyRandom;

import java.io.File;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Random;

/**
 * Starts one Commander game in Forge, with no window, and connects to Python.
 *
 * Usage (Python normally runs this for you, see showdown/bridge/forge_process.py):
 *
 *   java ... showdown.bridge.BridgeMain --port 5555 [--seed 42]
 *        --seat path/to/zidane.dck=python
 *        --seat path/to/kuja.dck=forge
 *        ... (2 to 4 seats)
 *
 * "=python" seats ask Python for their decisions; "=forge" seats use Forge's
 * built-in AI, which is handy as an opponent and as a yardstick.
 *
 * The working directory must be Forge's "forge-gui" folder, because Forge
 * loads its card scripts from the "res" folder there.
 */
public final class BridgeMain {
    private BridgeMain() { }

    private record Seat(String deckPath, boolean python) { }

    public static void main(String[] args) throws Exception {
        // Never open a window: we run as a background process.
        System.setProperty("java.awt.headless", "true");

        int port = -1;
        Long seed = null;
        List<Seat> seats = new ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--port" -> port = Integer.parseInt(args[++i]);
                case "--seed" -> seed = Long.parseLong(args[++i]);
                case "--seat" -> {
                    String[] parts = args[++i].split("=", 2);
                    seats.add(new Seat(parts[0], parts.length > 1 && parts[1].equalsIgnoreCase("python")));
                }
                default -> throw new IllegalArgumentException("Unknown argument: " + args[i]);
            }
        }
        if (port < 0 || seats.size() < 2 || seats.size() > 4) {
            System.err.println("Need --port and 2 to 4 --seat arguments");
            System.exit(2);
        }

        try (BridgeConnection bridge = new BridgeConnection("127.0.0.1", port)) {
            int code = runGame(bridge, seats, seed);
            System.exit(code);
        }
    }

    private static int runGame(BridgeConnection bridge, List<Seat> seats, Long seed) {
        // Forge needs a "GUI interface" even when nothing is shown; the desktop
        // one works fine headless. Then load cards, editions, AI profiles, ...
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, null);
        if (seed != null) {
            MyRandom.setRandom(new Random(seed));
        }

        GameRules rules = new GameRules(GameType.Commander);
        rules.setAppliedVariants(EnumSet.of(GameType.Commander));

        PythonPlayerController.LogCursor log = new PythonPlayerController.LogCursor();
        List<RegisteredPlayer> players = new ArrayList<>();
        JsonArray seatInfo = new JsonArray();
        for (int i = 0; i < seats.size(); i++) {
            Seat seat = seats.get(i);
            Deck deck = DeckSerializer.fromFile(new File(seat.deckPath()));
            if (deck == null) {
                throw new IllegalArgumentException("Could not read deck " + seat.deckPath());
            }
            // Player names must be unique; the deck name makes logs readable.
            String name = (i + 1) + "-" + deck.getName();
            RegisteredPlayer rp = RegisteredPlayer.forCommander(deck);
            if (seat.python()) {
                rp.setPlayer(new PythonLobbyPlayer(name, bridge, log));
            } else {
                rp.setPlayer(GamePlayerUtil.createAiPlayer(name, i));
            }
            players.add(rp);

            JsonObject sj = new JsonObject();
            sj.addProperty("seat", i);
            sj.addProperty("name", name);
            sj.addProperty("deck", deck.getName());
            sj.addProperty("controller", seat.python() ? "python" : "forge");
            seatInfo.add(sj);
        }

        JsonObject start = new JsonObject();
        start.addProperty("type", "game_start");
        start.add("seats", seatInfo);
        bridge.send(start);

        Match match = new Match(rules, players, "Commander AI Showdown");
        Game game = match.createGame();
        game.setNoGUIUser();
        game.subscribeToEvents(new SpectatorFeed(game, bridge, log)); // live feed for the viewer
        try {
            match.startGame(game); // runs the whole game on this thread
        } catch (RuntimeException e) {
            e.printStackTrace();
            game.setGameOver(GameEndReason.Draw);
        }

        log.flush(game, bridge);
        JsonObject over = new JsonObject();
        over.addProperty("type", "game_over");
        GameOutcome outcome = game.getOutcome();
        boolean draw = outcome == null || outcome.isDraw();
        over.addProperty("draw", draw);
        over.addProperty("winner", draw ? null : outcome.getWinningLobbyPlayer().getName());
        over.addProperty("turns", outcome == null ? game.getPhaseHandler().getTurn() : outcome.getLastTurnNumber());
        JsonArray life = new JsonArray();
        for (Player p : game.getRegisteredPlayers()) {
            JsonObject pj = new JsonObject();
            pj.addProperty("name", p.getName());
            pj.addProperty("life", p.getLife());
            pj.addProperty("lost", p.hasLost());
            life.add(pj);
        }
        over.add("players", life);
        bridge.send(over);
        return 0;
    }
}
