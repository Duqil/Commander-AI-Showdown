package showdown.bridge;

import com.google.gson.Gson;
import com.google.gson.JsonObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.io.UncheckedIOException;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

/**
 * The wire between Forge (Java) and the Python side.
 *
 * Protocol: one JSON object per line ("JSON Lines"), over a local TCP socket.
 * Python starts a server, launches this Java program, and Java connects back.
 *
 * Java sends three kinds of messages:
 *   - {"type": "event", ...}     something happened (log lines), no answer expected
 *   - {"type": "decision", "id": 7, ...}   Forge needs a choice, Python MUST answer
 *   - {"type": "game_over", ...}  the game ended
 *
 * Python answers a decision with {"id": 7, "choice": ...}.
 *
 * Forge runs the whole game on a single thread, so at most one decision is
 * waiting at any time. That keeps this class simple: send, then block until
 * the answer line arrives.
 */
public final class BridgeConnection implements AutoCloseable {
    private final Socket socket;
    private final BufferedReader in;
    private final PrintWriter out;
    private final Gson gson = new Gson();
    private int nextDecisionId = 1;

    public BridgeConnection(String host, int port) throws IOException {
        this.socket = new Socket(host, port);
        this.in = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
        this.out = new PrintWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8), true);
    }

    /** Fire-and-forget message (events, game over). */
    public synchronized void send(JsonObject message) {
        out.println(gson.toJson(message));
    }

    /**
     * Ask Python to decide something and wait for the answer.
     *
     * @return the "choice" field of Python's reply (may be JSON null)
     */
    public synchronized JsonObject ask(JsonObject decision) {
        int id = nextDecisionId++;
        decision.addProperty("type", "decision");
        decision.addProperty("id", id);
        out.println(gson.toJson(decision));
        try {
            String line = in.readLine();
            if (line == null) {
                throw new IOException("Python closed the connection");
            }
            JsonObject reply = gson.fromJson(line, JsonObject.class);
            int replyId = reply.get("id").getAsInt();
            if (replyId != id) {
                throw new IOException("Expected answer to decision " + id + " but got " + replyId);
            }
            return reply;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /**
     * Wait for Python's next message outside a decision (e.g. "next_game").
     *
     * @return the message, or null when Python closed the connection
     */
    public JsonObject receive() {
        try {
            String line = in.readLine();
            return line == null ? null : gson.fromJson(line, JsonObject.class);
        } catch (IOException e) {
            return null; // a broken connection means the same as "no more games"
        }
    }

    @Override
    public void close() throws IOException {
        socket.close();
    }
}
