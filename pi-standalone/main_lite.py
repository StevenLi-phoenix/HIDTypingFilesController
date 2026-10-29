#!/usr/bin/env python3
"""Type an ASCII file through the USB HID gadget. Stdlib only, no venv, no deps.

Same key map as HIDcontroller/main.py, but:
  - stdlib only (no tqdm, no dotenv) -> works on a fresh Raspbian card
  - --wait    : do not write until /sys/class/udc/*/state says "configured"
  - --delay N : wait N seconds after the link is up (time to focus the target window)
  - writes are non-blocking with a timeout, so a dead host cannot hang the run
  - progress goes to stdout every --progress chars (default 500)

Usage:
  sudo python3 main_lite.py PAYLOAD.txt --wait --delay 15
  sudo python3 main_lite.py PAYLOAD.txt              # link already up
  sudo python3 main_lite.py PAYLOAD.txt --dry-run    # verify chars only
"""
import errno
import os
import select
import sys
import time

DEV = os.environ.get("HID_DEVICE", "/dev/hidg0")
UDC_DIR = "/sys/class/udc"
EMPTY_REPORT = b"\x00" * 8


def build_map():
    km = {}
    SHIFT, NONE = 0x02, 0x00

    def add(ch, code, shift=False):
        km[ch] = (SHIFT if shift else NONE, code)

    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        add(c, 0x04 + i)
        add(c.upper(), 0x04 + i, True)
    for c, code in zip("1234567890", (0x1E, 0x1F, 0x20, 0x21, 0x22,
                                     0x23, 0x24, 0x25, 0x26, 0x27)):
        add(c, code)
    add(" ", 0x2C)
    add("\n", 0x28)
    add("\r", 0x28)
    add("\t", 0x2B)
    for c, code in (("-", 0x2D), ("=", 0x2E), ("[", 0x2F), ("]", 0x30),
                    ("\\", 0x31), (";", 0x33), ("'", 0x34), ("`", 0x35),
                    (",", 0x36), (".", 0x37), ("/", 0x38)):
        add(c, code)
    for c, code in (("!", 0x1E), ("@", 0x1F), ("#", 0x20), ("$", 0x21),
                    ("%", 0x22), ("^", 0x23), ("&", 0x24), ("*", 0x25),
                    ("(", 0x26), (")", 0x27), ("_", 0x2D), ("+", 0x2E),
                    ("{", 0x2F), ("}", 0x30), ("|", 0x31), (":", 0x33),
                    ('"', 0x34), ("~", 0x35), ("<", 0x36), (">", 0x37),
                    ("?", 0x38)):
        add(c, code, True)
    return km


def udc_state():
    """Best-effort UDC state: "configured" when a host has enumerated us."""
    seen = "unknown"
    try:
        names = os.listdir(UDC_DIR)
    except OSError:
        return seen
    for name in names:
        try:
            with open(os.path.join(UDC_DIR, name, "state")) as fh:
                s = fh.read().strip()
        except OSError:
            continue
        if s:
            seen = s
        if s == "configured":
            return s
    return seen


def wait_configured(timeout=None):
    t0 = time.time()
    last = None
    while True:
        s = udc_state()
        if s != last:
            print("udc: %s" % s, flush=True)
            last = s
        if s == "configured":
            return True
        if timeout and time.time() - t0 > timeout:
            return False
        time.sleep(2)


def write_report(fd, data, timeout=5.0):
    """One 8-byte report; never partial, never hangs forever."""
    deadline = time.time() + timeout
    while True:
        try:
            n = os.write(fd, data)
            if n != len(data):
                raise IOError("short write %d" % n)
            return
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EPIPE):
                raise
            if time.time() > deadline:
                raise IOError("host stopped consuming reports (%s)" % exc)
            select.select([], [fd], [], 0.2)


def type_text(text, press=0.01, release=0.02, progress=500):
    km = build_map()
    try:
        fd = os.open(DEV, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        print("cannot open %s: %s" % (DEV, exc), flush=True)
        return 2
    t0 = time.time()
    try:
        for i, ch in enumerate(text, 1):
            mod, code = km[ch]
            write_report(fd, bytes([mod, 0x00, code, 0, 0, 0, 0, 0]))
            if press:
                time.sleep(press)
            write_report(fd, EMPTY_REPORT)
            if release:
                time.sleep(release)
            if progress and i % progress == 0:
                print("  %d/%d  %.0fs" % (i, len(text), time.time() - t0),
                      flush=True)
    except KeyboardInterrupt:
        print("aborted at char %d" % i, flush=True)
        return 130
    except Exception as exc:
        print("FAILED at char %d: %s" % (i, exc), flush=True)
        return 2
    finally:
        os.close(fd)
    print("done: %d chars in %.0fs" % (len(text), time.time() - t0), flush=True)
    return 0


def main(argv):
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2
    path = args[0]
    wait = "--wait" in args
    dry = "--dry-run" in args
    delay = 0.0
    if "--delay" in args:
        delay = float(args[args.index("--delay") + 1])
    press, release = 0.01, 0.02
    if "--press" in args:
        press = float(args[args.index("--press") + 1])
    if "--release" in args:
        release = float(args[args.index("--release") + 1])

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    km = build_map()
    bad = [i for i, c in enumerate(text) if c not in km]
    print("file: %s  chars: %d  lines: %d  unsupported: %d"
          % (path, len(text), text.count("\n"), len(bad)), flush=True)
    if bad:
        for i in bad[:5]:
            print("  pos %d: %r" % (i, text[i]), flush=True)
        print("refusing to type: not all characters are typeable", flush=True)
        return 2
    print("verify: all %d characters typeable" % len(text), flush=True)
    if dry:
        return 0

    if wait and not wait_configured():
        print("link never came up", flush=True)
        return 2
    if delay:
        for s in range(int(delay), 0, -1):
            print("  typing in %ds" % s, flush=True)
            time.sleep(1)
    return type_text(text, press, release)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
