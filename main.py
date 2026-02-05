import os
import time
from typing import Tuple

import tqdm
from dotenv import load_dotenv

load_dotenv()

HID_DEVICE = os.getenv("HID_DEVICE", "/dev/hidg0")

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
    def __init__(self):
        self.device = open(HID_DEVICE, "wb", buffering=0)
        self.key_map = build_map()

    def __enter__(self):
        return self
    
    def send_key(self, key: str):
        binding: Tuple[int, int] = self.key_map.get(key)
        if binding is None:
            raise ValueError(f"Unsupported character: {key}")

        modifier, keycode = binding
        press_report = bytes([modifier, 0x00, keycode, 0x00, 0x00, 0x00, 0x00, 0x00])
        self.device.write(press_report)
        time.sleep(0.01)
        # Release all keys so modifiers don't get "stuck"
        self.device.write(EMPTY_REPORT)
        time.sleep(0.02)

    def __exit__(self, exc_type, exc_value, traceback):
        self.device.close()



def type_string(filename: str):
    if not os.path.exists(filename):
        raise FileNotFoundError(f"File not found: {filename}")
    with open(filename, "r") as file:
        content = file.read()
    with HIDKeyboard() as keyboard:
        for char in tqdm.tqdm(content, desc="Typing", unit="char"):
            keyboard.send_key(char)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("filename", type=str)
    args = parser.parse_args()
    type_string(args.filename)
