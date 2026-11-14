package showdown.bridge;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Reads every card script once before Forge does, and counts while it goes.
 *
 * Why: Forge starts by opening all ~34,000 small card files (res/cardsfolder)
 * to index them, and says nothing while it does. On a fast disk that takes a
 * second. On some Windows PCs it takes minutes (the virus scanner checks every
 * file the first time it is opened, and a hard disk has to seek for each one),
 * and the viewer sat at "5%" the whole time.
 *
 * So we open the files first, a few at a time, and report "Reading card files
 * 12,000 of 34,468". Afterwards the files are in Windows' memory cache (and
 * already scanned), so Forge's own pass is quick. We change nothing on disk.
 * On a fast disk the first 2,000 files show it within a fraction of a second,
 * and the rest are skipped.
 */
final class CardFileWarmup {
    private CardFileWarmup() { }

    // The first SAMPLE files read in under FAST_SAMPLE_NANOS (0.3 s) = a fast disk.
    // For comparison: one Windows PC needed about 5 ms per file, 10 s for these.
    private static final int SAMPLE = 2000;
    private static final long FAST_SAMPLE_NANOS = 300_000_000L;

    /** Read all card files, reporting progress from {@code from}% to {@code to}%. */
    static void run(BridgeConnection bridge, File folder, int from, int to) {
        List<File> files = new ArrayList<>();
        collect(folder, files);
        if (files.isEmpty()) {
            return;
        }
        int total = files.size();
        AtomicInteger done = new AtomicInteger();
        AtomicInteger lastPercent = new AtomicInteger(-1);
        // On a fast disk (most PCs) the first files come back almost at once.
        // Then this pass would only cost time, so the rest are skipped.
        long started = System.nanoTime();
        AtomicBoolean fastDisk = new AtomicBoolean(false);
        // A few threads at once: the virus scanner and the disk can work on
        // several files at the same time, so this is faster than one by one.
        ExecutorService pool = Executors.newFixedThreadPool(4);
        for (File file : files) {
            pool.execute(() -> {
                if (!fastDisk.get()) {
                    read(file);
                }
                int n = done.incrementAndGet();
                if (n == SAMPLE && System.nanoTime() - started < FAST_SAMPLE_NANOS) {
                    fastDisk.set(true);
                }
                int percent = from + (int) ((long) (to - from) * n / total);
                // Only send when the whole number changes (about 50 messages in all).
                int last = lastPercent.get();
                if (percent > last && lastPercent.compareAndSet(last, percent)) {
                    LoadingProgress.send(bridge, percent, String.format("Reading card files %,d of %,d", n, total));
                }
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(1, TimeUnit.HOURS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** Every file under {@code folder}, in its sub-folders too ("a", "b", ...). */
    private static void collect(File folder, List<File> out) {
        File[] entries = folder.listFiles();
        if (entries == null) {
            return; // not there (or not readable): Forge will report it
        }
        for (File f : entries) {
            if (f.isDirectory()) {
                collect(f, out);
            } else if (f.getName().endsWith(".txt")) {
                out.add(f);
            }
        }
    }

    private static void read(File file) {
        try (InputStream in = Files.newInputStream(file.toPath())) {
            in.readAllBytes(); // we only want the file read once; the bytes are thrown away
        } catch (IOException e) {
            // A file we can't read now, Forge can't either; it will say so itself.
        }
    }
}
