import logging
import os
import time
from typing import Tuple

import tqdm
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HID_DEVICE = os.getenv("HID_DEVICE", "/dev/hidg0")

DEFAULT_KEY_PRESS_SLEEP = 0.01
DEFAULT_KEY_RELEASE_SLEEP = 0.02

# HID keyboard report is always 8 bytes: modifier, reserved, then 6 key slots
EMPTY_REPORT = b"\x00\x00\x00\x00\x00\x00\x00\x00"

def build_map():
    # ASCII (US QWERTY) -> (modifier, keycode)
    # modifier: 0x00 none, 0x02 left shift
    key_map = {}

    SHIFT = 0x02
    NONE = 0x00

    def _add(ch, keycode, shift=False):
        key_map[ch] = (SHIFT if shift else NONE, keycode)

    # Letters
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        _add(c, 0x04 + i, False)
        _add(c.upper(), 0x04 + i, True)

    # Digits row
    for c, code in zip("1234567890", [0x1E,0x1F,0x20,0x21,0x22,0x23,0x24,0x25,0x26,0x27]):
        _add(c, code, False)

    # Whitespace / control (common)
    _add(" ", 0x2C, False)     # Space
    _add("\n", 0x28, False)    # Enter
    _add("\r", 0x28, False)    # Enter (CR)
    _add("\t", 0x2B, False)    # Tab

    # Punctuation (unshifted)
    _add("-", 0x2D, False)
    _add("=", 0x2E, False)
    _add("[", 0x2F, False)
    _add("]", 0x30, False)
    _add("\\",0x31, False)
    _add(";", 0x33, False)
    _add("'", 0x34, False)
    _add("`", 0x35, False)
    _add(",", 0x36, False)
    _add(".", 0x37, False)
    _add("/", 0x38, False)

    # Shifted symbols on the same keys
    _add("!", 0x1E, True)
    _add("@", 0x1F, True)
    _add("#", 0x20, True)
    _add("$", 0x21, True)
    _add("%", 0x22, True)
    _add("^", 0x23, True)
    _add("&", 0x24, True)
    _add("*", 0x25, True)
    _add("(", 0x26, True)
    _add(")", 0x27, True)

    _add("_", 0x2D, True)
    _add("+", 0x2E, True)
    _add("{", 0x2F, True)
    _add("}", 0x30, True)
    _add("|", 0x31, True)
    _add(":", 0x33, True)
    _add('"', 0x34, True)
    _add("~", 0x35, True)
    _add("<", 0x36, True)
    _add(">", 0x37, True)
    _add("?", 0x38, True)
    
    return key_map

class HIDKeyboard:
    def __init__(
        self,
        key_press_sleep: float = DEFAULT_KEY_PRESS_SLEEP,
        key_release_sleep: float = DEFAULT_KEY_RELEASE_SLEEP,
    ):
        self.device = open(HID_DEVICE, "wb", buffering=0)
        self.key_map = build_map()
        self.key_press_sleep = key_press_sleep
        self.key_release_sleep = key_release_sleep
        logger.info(
            "HIDKeyboard initialized (press_sleep=%.3fs, release_sleep=%.3fs)",
            self.key_press_sleep,
            self.key_release_sleep,
        )

    def __enter__(self):
        return self

    def _build_report(self, key: str) -> bytes:
        """Build press + release reports for a single key."""
        binding: Tuple[int, int] | None = self.key_map.get(key)
        if binding is None:
            raise ValueError(f"Unsupported character: {key}")
        modifier, keycode = binding
        press = bytes([modifier, 0x00, keycode, 0x00, 0x00, 0x00, 0x00, 0x00])
        return press + EMPTY_REPORT

    def send_key(self, key: str) -> None:
        report = self._build_report(key)
        # Write press report
        self.device.write(report[:8])
        if self.key_press_sleep > 0:
            time.sleep(self.key_press_sleep)
        # Release all keys so modifiers don't get "stuck"
        self.device.write(report[8:])
        if self.key_release_sleep > 0:
            time.sleep(self.key_release_sleep)

    def send_keys_batch(self, text: str) -> None:
        """Write all press+release reports in a single syscall."""
        buf = bytearray()
        for char in text:
            buf += self._build_report(char)
        self.device.write(buf)
        logger.info("Batch-wrote %d reports (%d bytes)", len(text) * 2, len(buf))

    def __exit__(self, exc_type, exc_value, traceback):
        self.device.close()



def verify_chars(content: str, key_map: dict) -> list:
    """Check all characters are supported before typing. Returns list of unsupported chars."""
    unsupported = []
    for i, char in enumerate(content):
        if char not in key_map:
            unsupported.append((i, char, repr(char)))
    return unsupported


def type_string(
    filename: str,
    key_press_sleep: float = DEFAULT_KEY_PRESS_SLEEP,
    key_release_sleep: float = DEFAULT_KEY_RELEASE_SLEEP,
    batch: bool = False,
) -> None:
    if not os.path.exists(filename):
        raise FileNotFoundError(f"File not found: {filename}")
    with open(filename, "r") as file:
        content = file.read()

    # Verify all characters before typing
    key_map = build_map()
    unsupported = verify_chars(content, key_map)
    if unsupported:
        logger.error("%d unsupported character(s) found", len(unsupported))
        for pos, char, char_repr in unsupported[:10]:  # Show first 10
            logger.error("  Position %d: %s", pos, char_repr)
        if len(unsupported) > 10:
            logger.error("  ... and %d more", len(unsupported) - 10)
        raise ValueError(f"Cannot type: {len(unsupported)} unsupported character(s)")

    logger.info("Verified %d characters OK", len(content))

    with HIDKeyboard(key_press_sleep, key_release_sleep) as keyboard:
        if batch:
            logger.info("Batch mode: writing all reports in one syscall")
            keyboard.send_keys_batch(content)
        else:
            for char in tqdm.tqdm(content, desc="Typing", unit="char"):
                keyboard.send_key(char)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Type file contents via HID keyboard")
    parser.add_argument("filename", type=str, help="File to type")
    parser.add_argument(
        "--key-press-sleep",
        type=float,
        default=DEFAULT_KEY_PRESS_SLEEP,
        help=f"Sleep after key press in seconds (default: {DEFAULT_KEY_PRESS_SLEEP})",
    )
    parser.add_argument(
        "--key-release-sleep",
        type=float,
        default=DEFAULT_KEY_RELEASE_SLEEP,
        help=f"Sleep after key release in seconds (default: {DEFAULT_KEY_RELEASE_SLEEP})",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Write all reports in a single syscall (fastest, no progress bar)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    type_string(
        args.filename,
        args.key_press_sleep,
        args.key_release_sleep,
        args.batch,
    )
