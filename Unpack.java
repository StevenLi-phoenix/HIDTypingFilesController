import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.zip.*;

/*
 * Unpacks payloads made by hid_pack.py. ASCII only and no tabs: it is itself typed over HID.
 *   JDK 11+: java --source 11 Unpack.txt payload.txt [outdir] [--dry-run] [--no-backup] [--apply-deletes]
 *   JDK 8  : copy Unpack.txt Unpack.java && javac Unpack.java && java Unpack payload.txt [outdir] [flags]
 * payload.txt is ONE payload, or a DIRECTORY of a pack set's numbered portions: the good ones are
 * applied, then any missing/failed portion is named on stderr (exit 2) to retype. Overwrites keep a
 * NAME.bak.yyyyMMddHHmmss backup; deletions need --apply-deletes. Exit 0 ok / 1 usage/IO / 2 bad payload.
 */
public class Unpack {
  static final String BEGIN = "-----BEGIN HID PAYLOAD-----";
  static final String END = "-----END HID PAYLOAD-----";
  static final byte[] MAGIC = {'H', 'I', 'D', 'P', 1};
  static final int FILE = 1, EXEC = 2, DELETE = 3;

  static class Bad extends Exception { Bad(String m) { super(m); } }
  static class Item { int kind; String path; byte[] data; }
  static class Portion { String file, set, labels, problem; int id, count; boolean ok; }

  public static void main(String[] args) {
    List<String> pos = new ArrayList<>();
    boolean dry = false, backup = true, del = false;
    for (String a : args) {
      if (a.equals("--dry-run")) dry = true;
      else if (a.equals("--no-backup")) backup = false;
      else if (a.equals("--apply-deletes")) del = true;
      else pos.add(a);
    }
    if (pos.isEmpty() || pos.size() > 2) {
      System.err.println("usage: Unpack payload.txt|payloaddir [outdir] [--dry-run] [--no-backup] [--apply-deletes]");
      System.exit(1);
    }
    Path src = Paths.get(pos.get(0));
    Path out = Paths.get(pos.size() > 1 ? pos.get(1) : ".");
    try {
      System.exit(Files.isDirectory(src) ? runSet(src, out, dry, backup, del) : runOne(src, out, dry, backup, del));
    } catch (IOException e) {
      System.err.println("ERROR: " + e);
      System.exit(1);
    }
  }

  // One payload; failures name the portion/pack-set.
  static int runOne(Path src, Path out, boolean dry, boolean backup, boolean del) throws IOException {
    String text = readText(src);
    String w = where(peekHead(text));
    try {
      List<Item> items = decode(text);
      System.out.println((w.isEmpty() ? "" : w + " -> ") + src.getFileName());
      apply(items, out, dry, backup, del);
      return 0;
    } catch (Bad e) {
      System.err.println("BAD PAYLOAD, nothing written" + (w.isEmpty() ? "" : " [" + w + "]") + ":\n  " + e.getMessage());
      return 2;
    }
  }

  // A directory of portions: apply the good ones, name any missing/failed.
  static int runSet(Path dir, Path out, boolean dry, boolean backup, boolean del) throws IOException {
    List<Portion> parts = new ArrayList<>();
    int rc = 0;
    DirectoryStream<Path> ds = Files.newDirectoryStream(dir);
    for (Path f : ds) {
      if (!Files.isRegularFile(f)) continue;
      String text = readText(f);
      if (text.indexOf(BEGIN) < 0) continue;  // not a payload
      Map<String, String> h = peekHead(text);
      Portion p = new Portion();
      p.file = f.getFileName().toString();
      p.set = or(h.get("pack-set"), "");
      p.labels = or(h.get("pack-labels"), "");
      p.id = parseInt(h.get("pack-id"));
      p.count = parseInt(h.get("pack-count"));
      try {
        List<Item> items = decode(text);
        System.out.println("-- " + (where(h).isEmpty() ? p.file : where(h) + " -> " + p.file));
        apply(items, out, dry, backup, del);
        p.ok = true;
      } catch (Bad e) {
        p.problem = e.getMessage().replace("\n", "; ");
      }
      parts.add(p);
    }
    ds.close();
    Map<String, List<Portion>> sets = new LinkedHashMap<>();
    for (Portion p : parts) {
      if (p.count > 1) {
        List<Portion> l = sets.get(p.set);
        if (l == null) sets.put(p.set, l = new ArrayList<Portion>());
        l.add(p);
      } else if (!p.ok) {
        rc = 2;
        System.err.println("FAILED  " + p.file + " (nothing written): " + p.problem);
      }
    }
    for (Map.Entry<String, List<Portion>> e : sets.entrySet()) {
      List<Portion> ps = e.getValue();
      int n = 0;
      for (Portion p : ps) n = Math.max(n, p.count);
      Set<Integer> seen = new TreeSet<>();
      List<String> bad = new ArrayList<>();
      for (Portion p : ps) {
        seen.add(p.id);
        if (!p.ok) bad.add("portion " + p.id + "/" + n + " (" + labelAt(ps, p.id) + ") FAILED [" + p.file + "] " + p.problem);
      }
      for (int i = 1; i <= n; i++) {
        if (!seen.contains(i)) bad.add("portion " + i + "/" + n + " (" + labelAt(ps, i) + ") MISSING (not found)");
      }
      if (bad.isEmpty()) {
        System.out.println("pack-set " + e.getKey() + ": complete (" + n + "/" + n + ")");
        continue;
      }
      rc = 2;
      System.err.println();
      System.err.println("!!! PACK SET " + e.getKey() + " INCOMPLETE - retype these !!!");
      for (String s : bad) System.err.println("  " + s);
    }
    return rc;
  }

  // label of portion id, from pack-labels of any part.
  static String labelAt(List<Portion> ps, int id) {
    for (Portion p : ps) {
      if (p.labels.isEmpty()) continue;
      String[] a = p.labels.split(",");
      if (id >= 1 && id <= a.length) return a[id - 1];
    }
    return "?";
  }

  static String where(Map<String, String> h) {
    if (h.get("pack-id") == null) return "";
    return "portion " + h.get("pack-id") + "/" + or(h.get("pack-count"), "?")
        + " (" + or(h.get("pack-label"), "?") + ") of pack-set " + or(h.get("pack-set"), "?");
  }

  static String or(String s, String dflt) { return s == null || s.isEmpty() ? dflt : s; }

  // The payload was typed into a .txt: Notepad may have saved it as UTF-8 (BOM) or "Unicode" (UTF-16).
  static String readText(Path p) throws IOException {
    byte[] b = Files.readAllBytes(p);
    int b0 = b.length > 0 ? b[0] & 0xFF : 0, b1 = b.length > 1 ? b[1] & 0xFF : 0;
    if (b0 == 0xFF && b1 == 0xFE) return new String(b, 2, b.length - 2, StandardCharsets.UTF_16LE);
    if (b0 == 0xFE && b1 == 0xFF) return new String(b, 2, b.length - 2, StandardCharsets.UTF_16BE);
    return new String(b, StandardCharsets.ISO_8859_1);  // a UTF-8 BOM just ends up before the BEGIN marker
  }

  // Lenient header read: works even when the body is corrupt.
  static Map<String, String> peekHead(String text) {
    Map<String, String> h = new HashMap<>();
    boolean in = false;
    for (String raw : text.split("\r?\n", -1)) {
      String t = raw.trim();
      if (t.endsWith(BEGIN)) { in = true; continue; }
      if (!in) continue;
      if (t.endsWith(END)) break;
      int c = t.indexOf(": ");
      if (c > 0) h.put(t.substring(0, c), t.substring(c + 2).trim());
    }
    return h;
  }

  static List<Item> decode(String text) throws Bad {
    String[] raw = text.split("\r?\n", -1);
    int begin = -1, end = -1;
    for (int i = 0; i < raw.length; i++) {
      String t = raw[i].trim();
      if (begin < 0 && t.endsWith(BEGIN)) begin = i;
      else if (begin >= 0 && t.endsWith(END)) { end = i; break; }
    }
    if (begin < 0) throw new Bad("no BEGIN marker: is this a hid_pack payload?");
    if (end < 0) throw new Bad("no END marker: the payload was cut off, retype the tail");

    Map<String, String> head = new HashMap<>();
    List<String> problems = new ArrayList<>();
    StringBuilder b64 = new StringBuilder();
    int expect = 1;
    for (int i = begin + 1; i < end; i++) {
      String l = raw[i].trim();
      if (l.isEmpty()) continue;
      if (l.indexOf(':') >= 0) {  // header line; base64 never contains ':'
        int c = l.indexOf(": ");
        if (c > 0) head.put(l.substring(0, c), l.substring(c + 2).trim());
        continue;
      }
      if (l.length() < 13 || !l.substring(0, 4).matches("[0-9]{4}")) {
        problems.add("file line " + (i + 1) + ": not a payload line: " + shorten(l));
        continue;
      }
      int seq = Integer.parseInt(l.substring(0, 4));
      String data = l.substring(4, l.length() - 8), crc = l.substring(l.length() - 8);
      CRC32 c32 = new CRC32();
      c32.update(data.getBytes(StandardCharsets.US_ASCII));
      if (!String.format("%08x", c32.getValue()).equalsIgnoreCase(crc)) {
        problems.add(String.format("seq %04d (file line %d): checksum mismatch, retype this line", seq, i + 1));
      }
      if (seq > expect) problems.add(missing(expect, seq - 1));
      else if (seq < expect) problems.add(String.format("seq %04d (file line %d): duplicated or out of order", seq, i + 1));
      expect = seq + 1;
      b64.append(data);
    }
    int total = parseInt(head.get("lines"));
    if (total >= expect) problems.add(missing(expect, total));
    if (!problems.isEmpty()) {
      int n = problems.size();
      if (n > 20) { problems = new ArrayList<>(problems.subList(0, 20)); problems.add("... and " + (n - 20) + " more"); }
      throw new Bad(String.join("\n  ", problems));
    }

    byte[] gz;
    try {
      gz = Base64.getDecoder().decode(b64.toString());
    } catch (IllegalArgumentException e) {
      throw new Bad("base64 decode failed: " + e.getMessage());
    }
    String sha = hex(gz);
    if (!sha.equalsIgnoreCase(head.get("sha256"))) {
      throw new Bad("sha256 mismatch: header " + head.get("sha256") + ", computed " + sha
          + " (every line checks out, so the header itself was mistyped or lines were swapped)");
    }
    try {
      return container(gz);
    } catch (IOException e) {
      throw new Bad("corrupt archive: " + e);
    }
  }

  static List<Item> container(byte[] gz) throws IOException, Bad {
    DataInputStream in = new DataInputStream(new BufferedInputStream(new GZIPInputStream(new ByteArrayInputStream(gz))));
    byte[] magic = new byte[MAGIC.length];
    in.readFully(magic);
    if (!Arrays.equals(magic, MAGIC)) throw new Bad("unknown payload version");
    List<Item> items = new ArrayList<>();
    while (true) {
      int kind = in.readUnsignedByte();
      if (kind == 0) break;
      if (kind > DELETE) throw new Bad("unknown entry kind " + kind);
      int n = in.readInt();
      if (n < 0 || n > 4096) throw new Bad("bad path length " + n);
      byte[] p = new byte[n];
      in.readFully(p);
      Item it = new Item();
      it.kind = kind;
      it.path = new String(p, StandardCharsets.UTF_8);
      checkPath(it.path);
      if (kind != DELETE) {
        int len = in.readInt();
        if (len < 0) throw new Bad("bad file length for " + it.path);
        it.data = new byte[len];
        in.readFully(it.data);
      }
      items.add(it);
    }
    return items;
  }

  static void checkPath(String p) throws Bad {
    boolean bad = p.isEmpty() || p.startsWith("/") || p.indexOf('\\') >= 0 || p.indexOf(':') >= 0 || p.indexOf('\0') >= 0;
    for (String seg : p.split("/", -1)) {
      if (seg.isEmpty() || seg.equals(".") || seg.equals("..")) bad = true;
    }
    if (bad) throw new Bad("unsafe path refused: \"" + p + "\"");
  }

  static void apply(List<Item> items, Path out, boolean dry, boolean backup, boolean del) throws IOException {
    String stamp = new SimpleDateFormat("yyyyMMddHHmmss").format(new Date());
    String tag = dry ? "[dry-run] " : "";
    String jnu = String.valueOf(System.getProperty("sun.jnu.encoding"));
    int nNew = 0, nChg = 0, nSame = 0, nDel = 0, nKeep = 0;
    for (Item it : items) {
      if (!dry && File.separatorChar == '/' && !jnu.toUpperCase().contains("UTF") && !it.path.matches("\\A\\p{ASCII}*\\z")) {
        throw new IOException("non-ASCII file name " + it.path + " but sun.jnu.encoding=" + jnu
            + "; rerun with LC_ALL=C.UTF-8 (or another UTF-8 locale)");
      }
      Path t = out.resolve(it.path);
      boolean exists = Files.exists(t);
      if (it.kind == DELETE) {
        if (!exists) {
          System.out.println(tag + "GONE   " + it.path);
        } else if (!del) {
          System.out.println(tag + "DEL?   " + it.path + "   (listed only; --apply-deletes to remove)");
          nKeep++;
        } else {
          if (!dry) {
            if (backup) Files.move(t, Paths.get(t + ".bak." + stamp));
            else Files.delete(t);
          }
          System.out.println(tag + "DEL    " + it.path);
          nDel++;
        }
        continue;
      }
      if (exists && Arrays.equals(Files.readAllBytes(t), it.data)) {
        System.out.println(tag + "SAME   " + it.path);
        nSame++;
        continue;
      }
      if (!dry) {
        if (t.getParent() != null) Files.createDirectories(t.getParent());
        if (exists && backup) Files.move(t, Paths.get(t + ".bak." + stamp));
        Files.write(t, it.data);
        if (it.kind == EXEC) t.toFile().setExecutable(true, false);
      }
      System.out.println(tag + (exists ? "CHG    " : "NEW    ") + it.path + "  (" + it.data.length + " B)");
      if (exists) nChg++; else nNew++;
    }
    System.out.println(tag + "done: " + nNew + " new, " + nChg + " changed, " + nSame + " same, "
        + nDel + " deleted, " + nKeep + " deletes not applied");
  }

  static String missing(int from, int to) {
    return from == to ? String.format("seq %04d is missing", from) : String.format("seq %04d-%04d are missing", from, to);
  }

  static int parseInt(String s) {
    try { return Integer.parseInt(s.trim()); } catch (RuntimeException e) { return 0; }
  }

  static String shorten(String s) {
    return s.length() > 40 ? s.substring(0, 40) + "..." : s;
  }

  static String hex(byte[] data) {
    try {
      StringBuilder sb = new StringBuilder();
      for (byte b : MessageDigest.getInstance("SHA-256").digest(data)) sb.append(String.format("%02x", b));
      return sb.toString();
    } catch (java.security.NoSuchAlgorithmException e) {
      throw new IllegalStateException(e);
    }
  }
}
