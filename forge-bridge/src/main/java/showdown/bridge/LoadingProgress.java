package showdown.bridge;

import com.google.gson.JsonObject;

import forge.gui.interfaces.IProgressBar;

/**
 * Tells Python how far Forge has got while it loads, so the viewer can show
 * "Loading card rules… 40%" instead of a frozen "Loading rules…".
 *
 * Forge's own splash screen uses an IProgressBar. We hand Forge this one
 * instead, and it turns every update into a small message:
 *
 *   {"type": "loading", "percent": 42, "text": "Loading card rules"}
 *
 * "percent" is for the whole wait before the first card is dealt, not just
 * this step. Python says 0-5% (starting Java), reading the card files
 * (CardFileWarmup) takes FROM..CARDS_READ, the rest of Forge's loading
 * CARDS_READ..TO, then reading decks and dealing fill the rest.
 */
final class LoadingProgress implements IProgressBar {
    // Loading the rules is the slow part, so it gets most of the bar.
    static final int FROM = 5;
    static final int CARDS_READ = 55;
    static final int TO = 80;

    private final BridgeConnection bridge;
    private int maximum = 1000;
    private int lastPercent = -1;
    private String text = "Loading card rules";

    LoadingProgress(BridgeConnection bridge) {
        this.bridge = bridge;
    }

    /** Send one loading step to Python (also usable outside Forge's loading). */
    static void send(BridgeConnection bridge, int percent, String text) {
        JsonObject msg = new JsonObject();
        msg.addProperty("type", "loading");
        msg.addProperty("percent", percent);
        msg.addProperty("text", text);
        bridge.send(msg);
    }

    @Override
    public synchronized void setDescription(String s0) {
        if (s0 != null && !s0.isBlank()) {
            text = s0.replace("...", "").replace("…", "").trim();
            lastPercent = -1; // send the new step name even if the number didn't move
        }
    }

    @Override
    public synchronized void setValue(int progress) {
        int percent = CARDS_READ + (int) ((long) (TO - CARDS_READ) * Math.max(0, Math.min(progress, maximum)) / Math.max(1, maximum));
        if (percent != lastPercent) { // Forge reports hundreds of times; send only real changes
            lastPercent = percent;
            send(bridge, percent, text);
        }
    }

    @Override
    public synchronized void setMaximum(int maximum0) {
        maximum = Math.max(1, maximum0);
    }

    @Override
    public synchronized int getMaximum() {
        return maximum;
    }

    // The rest only matters for Forge's own on-screen bar.
    @Override public void reset() { }
    @Override public void setShowETA(boolean b0) { }
    @Override public void setShowCount(boolean b0) { }
    @Override public void setPercentMode(boolean percentMode0) { }
}
