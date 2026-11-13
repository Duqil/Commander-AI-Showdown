package showdown.bridge;

import forge.game.Game;

/**
 * A safety net against games that never end.
 *
 * Some board states loop forever (an infinite combo nobody stops) or grow
 * so huge that the game crawls. One such game used to stall a whole night
 * of training. So a game ends as a DRAW when it reaches either limit:
 *
 *   MAX_TURNS        turns in total (counting every player's turn; a normal
 *                    four-player game takes 30 to 50)
 *   MAX_FORGE_SECONDS  seconds Forge itself spent on this game. Time spent
 *                    waiting for Python (a paused viewer, a slow bot) doesn't
 *                    count, so pausing a game never ends it.
 *
 * Checked every time Python is asked something. A draw is a normal game
 * result: Python learns from it and counts it as a draw, not a crash.
 */
final class GameLimits {
    // Override for testing with java -Dshowdown.maxTurns=10 -Dshowdown.maxForgeSeconds=60
    static final int MAX_TURNS = Integer.getInteger("showdown.maxTurns", 200);
    static final long MAX_FORGE_SECONDS = Long.getLong("showdown.maxForgeSeconds", 15 * 60);

    private static long forgeNanos = 0;       // Forge's own time so far this game
    private static long since = System.nanoTime();
    private static String reached = null;     // why the game was stopped, or null

    private GameLimits() { }

    /** Thrown to stop the game; BridgeMain turns it into a draw. */
    static final class Reached extends RuntimeException {
        Reached(String why) {
            super(why);
        }
    }

    static synchronized void startGame() {
        forgeNanos = 0;
        since = System.nanoTime();
        reached = null;
    }

    /** Just before waiting for Python: stop the game if it went on too long. */
    static synchronized void beforeAsk(Game game) {
        forgeNanos += System.nanoTime() - since;
        int turn = game.getPhaseHandler().getTurn();
        if (turn > MAX_TURNS) {
            reached = "turn limit (" + MAX_TURNS + " turns)";
        } else if (forgeNanos / 1_000_000_000L > MAX_FORGE_SECONDS) {
            reached = "time limit (" + MAX_FORGE_SECONDS + " seconds of Forge time)";
        }
        if (reached != null) {
            throw new Reached(reached);
        }
    }

    /** Python answered: Forge's clock runs again. */
    static synchronized void afterAsk() {
        since = System.nanoTime();
    }

    /** Why the game was stopped, or null if it ended normally. */
    static synchronized String reached() {
        return reached;
    }
}
