#!/usr/bin/env python3
"""Pack files (Chinese, binary, anything) into a payload a HID keyboard can type.

HIDcontroller/main.py can only press printable ASCII, \\n, \\r and \\t; one Chinese character aborts
the whole run. So instead of typing the files, type ONE text payload (gzip + base64, every line
numbered and CRC32-checked) and unpack it on the target with Unpack.java (Java 8 compatible, standard library only).

    hid_pack.py --repo ../myrepo --from BASE -o myrepo.txt        # BASE -> working tree (+untracked)
    hid_pack.py --repo ../myrepo --from OLD --to HEAD             # ref -> ref, contents from git objects
    hid_pack.py --repo ../myrepo src ui/src                       # no --from: every file under the pathspecs
    hid_pack.py --repo ../myrepo --from BASE --only-untypeable    # only files a plain ASCII typing run cannot carry
    hid_pack.py --repo ../myrepo --from BASE --init -o out/       # first delivery: 1-sql.txt, 2-code.txt, framework-changes.txt
    hid_pack.py --repo ../myrepo --from BASE --split-by module -o parts/   # one typed .txt per top-level module

--split-by module (and --init) emit a "pack set": numbered portions sharing a pack-set id, each carrying
its pack-id/count/label in the header. Type them into one folder and run Unpack on the folder: it applies
every good portion, then names any portion that is missing or failed, so only that portion is retyped.

The source repo is only read. Payload format and Unpack usage: see README.md.
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import gzip
import hashlib
import logging
import os
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

log = logging.getLogger("hid_pack")

MAGIC = b"HIDP\x01"
FILE, EXEC, DELETE = 1, 2, 3  # container entry kinds (0 = end marker)
BEGIN = "-----BEGIN HID PAYLOAD-----"
END = "-----END HID PAYLOAD-----"
NOTES_PATH = "hid-notes/framework-changes.txt"  # where --init puts the framework diff on the target
LINE_CHARS = 256  # base64 characters per payload line (+4 seq +8 crc); must stay a multiple of 4
MAX_LINES = 9999  # the seq field is 4 digits
SECONDS_PER_CHAR = 0.03  # HIDKeyboard defaults: 0.01 s press + 0.02 s release

# What HIDcontroller/main.py:build_map() can press: printable ASCII, \n, \r, \t.
_TYPEABLE = bytes(range(0x20, 0x7F)) + b"\n\r\t"
# Never ship these to a machine by keyboard; matched against every path component (case-insensitive).
SECRET_GLOBS = (
    ".env", ".env.*", "*.key", "*.pem", "*.jks", "*.p12", "*.pfx", "*.keystore",
    "credentials", "credentials.*", "secrets", "secrets.*",
    "id_rsa*", "id_ed25519*", ".netrc", ".npmrc", ".pypirc",
)  # fmt: skip


class PackError(Exception):
    """A user-facing failure (bad ref, nothing to pack, payload too large)."""


@dataclass(frozen=True)
class Entry:
    kind: int  # FILE / EXEC / DELETE
    path: str  # POSIX, relative to the repo root
    data: bytes = b""


def is_typeable(data: bytes) -> bool:
    """True when every byte can be pressed by the HID keyboard."""
    return not data.translate(None, _TYPEABLE)


def is_secret(path: str) -> bool:
    return any(
        fnmatch.fnmatchcase(part.lower(), g) for part in PurePosixPath(path).parts for g in SECRET_GLOBS
    )


def is_mock_sql(path: str) -> bool:
    """*.sql under a mock/ directory is local-dev setup that may DROP shared tables: never ship it."""
    parts = PurePosixPath(path).parts
    return path.lower().endswith(".sql") and "mock" in parts[:-1]


def pack_entries(entries: list[Entry]) -> bytes:
    """Container: MAGIC, then [kind:1][pathlen:4][path utf8]([datalen:4][data])*, kind 0 ends. gzip -9."""
    buf = bytearray(MAGIC)
    for e in entries:
        p = e.path.encode("utf-8")
        buf += struct.pack(">BI", e.kind, len(p)) + p
        if e.kind != DELETE:
            buf += struct.pack(">I", len(e.data)) + e.data
    buf += b"\x00"
    return gzip.compress(bytes(buf), 9, mtime=0)


def _ascii(value: object) -> str:
    return str(value).encode("ascii", "replace").decode("ascii").replace("\n", " ").replace("\r", " ")


def armor(gz: bytes, meta: dict[str, object]) -> str:
    """Text payload: BEGIN, `key: value` header, numbered CRC'd base64 lines, END."""
    b64 = base64.b64encode(gz).decode("ascii")
    chunks = [b64[i : i + LINE_CHARS] for i in range(0, len(b64), LINE_CHARS)]
    if len(chunks) > MAX_LINES:
        raise PackError(
            f"payload too large: {len(chunks)} lines (max {MAX_LINES}); "
            "narrow it with pathspecs, --from, or --only-untypeable"
        )
    head = {"version": 1, **meta, "gz-bytes": len(gz), "lines": len(chunks), "sha256": hashlib.sha256(gz).hexdigest()}
    out = [BEGIN, *(f"{k}: {_ascii(v)}" for k, v in head.items()), ""]
    out += [f"{i:04d}{c}{zlib.crc32(c.encode('ascii')):08x}" for i, c in enumerate(chunks, 1)]
    out += [END, ""]
    text = "\n".join(out)
    if not is_typeable(text.encode("ascii")):  # defensive: HID aborts the whole run on one bad key
        raise PackError("internal error: payload contains a character the HID keyboard cannot type")
    return text


def pack_set_id(labels: list[str], meta: dict[str, object]) -> str:
    """Short id shared by every portion of one delivery, so the unpacker can spot a missing portion."""
    key = "\x1f".join(
        [_ascii(meta.get("source", "")), _ascii(meta.get("created", "")), _ascii(meta.get("from", "")),
         _ascii(meta.get("to", "")), *labels]
    )  # fmt: skip
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def safe_label(label: str) -> str:
    """A portion name that is safe as a typed file name: ASCII letters, digits, . _ - only."""
    s = "".join(c if (c.isascii() and (c.isalnum() or c in "._-")) else "_" for c in label)
    return s.strip("._-") or "part"



def _git(repo: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode != 0:
        raise PackError(f"git {' '.join(args[:2])} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def _split_z(raw: bytes) -> list[str]:
    return [p.decode("utf-8") for p in raw.split(b"\0") if p]


def list_changes(repo: Path, from_ref: str | None, to_ref: str | None, specs: list[str]) -> dict[str, str]:
    """{path: 'add' | 'modify' | 'delete'} vs from_ref. to_ref None = working tree (+ untracked, minus ignored)."""
    sp = ["--", *specs] if specs else []
    actions: dict[str, str] = {}
    if from_ref is None:
        for p in _split_z(_git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", *sp)):
            actions[p] = "add"
        return actions
    refs = [from_ref] if to_ref is None else [from_ref, to_ref]
    parts = _split_z(_git(repo, "diff", "--name-status", "-z", "--no-renames", *refs, *sp))
    for status, path in zip(parts[0::2], parts[1::2]):
        actions[path] = {"D": "delete", "A": "add"}.get(status[0], "modify")
    if to_ref is None:  # untracked files are invisible to git diff; a file still on disk beats a 'D'
        for p in _split_z(_git(repo, "ls-files", "-z", "--others", "--exclude-standard", *sp)):
            actions[p] = "add"
    return actions


def _tree_modes(repo: Path, ref: str, specs: list[str]) -> dict[str, str]:
    modes: dict[str, str] = {}
    for rec in _git(repo, "ls-tree", "-r", "-z", ref, *(["--", *specs] if specs else [])).split(b"\0"):
        if rec:
            meta, _, path = rec.partition(b"\t")
            modes[path.decode("utf-8")] = meta.split()[0].decode()
    return modes


def read_entries(
    repo: Path, actions: dict[str, str], to_ref: str | None, specs: list[str], from_ref: str | None
) -> list[Entry]:
    """Turn actions into entries, skipping secrets, symlinks, submodules and vanished files (with a warning)."""
    modes = _tree_modes(repo, to_ref, specs) if to_ref is not None else {}
    entries: list[Entry] = []
    for path in sorted(actions):
        if is_secret(path):
            log.warning("skip secret-looking file: %s", path)
            continue
        if actions[path] != "delete" and is_mock_sql(path):
            log.warning("skip local-mock SQL (may DROP shared tables): %s", path)
            continue
        if actions[path] == "delete":
            entries.append(Entry(DELETE, path))
        elif to_ref is not None:
            mode = modes.get(path, "")
            if mode in ("120000", "160000"):
                log.warning("skip %s: %s", "symlink" if mode == "120000" else "submodule", path)
                continue
            data = _git(repo, "cat-file", "blob", f"{to_ref}:{path}")
            entries.append(Entry(EXEC if mode == "100755" else FILE, path, data))
        else:
            f = repo / path
            if f.is_symlink() or not f.is_file():
                if from_ref is not None or f.exists():  # in full-tree mode a tracked-but-deleted file is just gone
                    log.warning("skip (not a regular file): %s", path)
                continue
            entries.append(Entry(EXEC if os.stat(f).st_mode & 0o111 else FILE, path, f.read_bytes()))
    return entries


def group_entries(entries: list[Entry], by: str) -> list[tuple[str, list[Entry]]]:
    """Ordered (label, entries) groups. `module` groups by top-level path component (root files -> "root")."""
    if by != "module":
        return [("all", entries)]
    groups: dict[str, list[Entry]] = {}
    for e in entries:
        parts = PurePosixPath(e.path).parts
        groups.setdefault(parts[0] if len(parts) > 1 else "root", []).append(e)
    return [(label, groups[label]) for label in sorted(groups)]


def _fmt_duration(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def framework_notes(repo: Path, from_ref: str, modified: list[str], n_deleted: int) -> str:
    """Human-readable txt: which framework files the project edited, with their diffs, for manual merging."""
    base = ["-c", "core.quotepath=off"]
    stat = _git(repo, *base, "diff", "--numstat", "-z", "--no-renames", from_ref, "--", *modified)
    counts = [rec.split("\t") for rec in stat.decode("utf-8", "replace").split("\0") if rec]
    out = [
        f"Framework files edited by {repo.name} since {from_ref}: {len(modified)} file(s).",
        "They are NOT part of the packed files. Merge each change into the target machine's copy by hand.",
        "",
        *(f"  +{a:<5} -{d:<5} {p}" for a, d, p in counts),
        "",
        f"Deleted since {from_ref}: {n_deleted} file(s), not packed and not listed.",
        "",
    ]
    for path in modified:
        diff = _git(repo, *base, "diff", "--no-color", "--no-renames", from_ref, "--", path)
        out += [f"{'=' * 20} {path} {'=' * 20}", diff.decode("utf-8", "replace").rstrip("\n"), ""]
    return "\n".join(out)


def make_payload(label: str, entries: list[Entry], meta: dict[str, object]) -> str:
    gz = pack_entries(entries)
    n_put = sum(e.kind != DELETE for e in entries)
    raw = sum(len(e.data) for e in entries)
    text = armor(gz, {**meta, "files": n_put, "deletes": len(entries) - n_put, "raw-bytes": raw})
    log.info(
        "%s: %d files + %d deletes, %s raw -> %s gzip -> %s typed chars in %s lines, typing ~%s",
        label, n_put, len(entries) - n_put, f"{raw:,}", f"{len(gz):,}", f"{len(text):,}", text.count("\n") - 4,
        _fmt_duration(len(text) * SECONDS_PER_CHAR),
    )  # fmt: skip
    return text


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("pathspec", nargs="*", help="limit to these git pathspecs (default: whole repo)")
    ap.add_argument("--repo", default=".", help="repo root (read only; default: .)")
    ap.add_argument("--from", dest="from_ref", help="base ref; pack what changed since it (default: every file)")
    ap.add_argument("--to", dest="to_ref", help="target ref, contents read from git (default: working tree)")
    ap.add_argument(
        "--init", action="store_true",
        help="first delivery onto a machine that has only the framework (= --from ref): pack only NEW files,\n"
        "split into 1-sql.txt + 2-code.txt; edited framework files and deletions are not packed, they are\n"
        "described in framework-changes.txt instead (-o is then a directory)",
    )  # fmt: skip
    ap.add_argument("--only-untypeable", action="store_true", help="only files HID cannot type (Chinese/binary)")
    ap.add_argument(
        "--split-by", choices=["none", "module"], default="none",
        help="split the payload into numbered .txt portions (one per top-level module) sharing a pack-set id,\n"
        "so a portion that fails to type can be retyped alone; -o is then a directory (like --init)",
    )  # fmt: skip
    ap.add_argument("-o", "--output", help="payload file (default: ./<repo>-hid-<timestamp>.txt); with --init or --split-by a directory")
    ap.add_argument("-n", "--dry-run", action="store_true", help="list what would be packed, write nothing")
    return ap


def run(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    top = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if top != repo:
        raise PackError(f"--repo must be the repo root: {top}")
    if args.to_ref and not args.from_ref:
        raise PackError("--to needs --from")
    if args.init and not args.from_ref:
        raise PackError("--init needs --from <ref of the framework the target already has>")
    if args.init and args.to_ref:
        raise PackError("--init packs the working tree; drop --to")

    actions = list_changes(repo, args.from_ref, args.to_ref, args.pathspec)
    modified = sorted(p for p, a in actions.items() if a == "modify") if args.init else []
    n_deleted = 0
    if args.init:  # the target already has the framework: only brand-new files travel
        n_deleted = sum(a == "delete" for a in actions.values())
        actions = {p: a for p, a in actions.items() if a == "add"}
    entries = read_entries(repo, actions, args.to_ref, args.pathspec, args.from_ref)
    if args.only_untypeable:
        entries = [
            e for e in entries
            if e.kind != DELETE and not (is_typeable(e.path.encode("utf-8")) and is_typeable(e.data))
        ]  # fmt: skip
    if not entries:
        raise PackError("nothing to pack")

    for e in entries:
        label = "DEL" if e.kind == DELETE else "PUT"
        print(f"{label}  {'' if e.kind == DELETE else f'{len(e.data):>9,}'}  {e.path}")

    meta = {
        "source": repo.name,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "from": args.from_ref or "-",
        "to": args.to_ref or ("WORKTREE" if args.from_ref else "-"),
    }
    notes = framework_notes(repo, args.from_ref, modified, n_deleted) if modified else None
    groups: list[tuple[str, str, list[Entry]]] = []  # (typed file name, portion label, entries)
    if args.init:
        sql = [e for e in entries if e.path.lower().endswith(".sql")]
        code = [e for e in entries if not e.path.lower().endswith(".sql")]
        if notes:
            code.append(Entry(FILE, NOTES_PATH, notes.encode("utf-8")))
        if sql:
            groups.append(("1-sql.txt", "sql", sql))
        if args.split_by == "module":
            for i, (label, es) in enumerate(group_entries(code, "module"), len(groups) + 1):
                groups.append((f"{i}-{safe_label(label)}.txt", label, es))
        elif code:
            groups.append(("2-code.txt", "code", code))
        mode, is_dir = "init", True
        target = Path(args.output) if args.output else Path.cwd() / f"{repo.name}-init"
    elif args.split_by == "module":
        groups = [(f"{i}-{safe_label(label)}.txt", label, es)
                  for i, (label, es) in enumerate(group_entries(entries, "module"), 1)]
        mode, is_dir = "split", True
        target = Path(args.output) if args.output else Path.cwd() / f"{repo.name}-hid-{datetime.now():%Y%m%d-%H%M%S}"
    else:
        groups, mode, is_dir = [("", "payload", entries)], "single", False
        target = Path(args.output) if args.output else Path.cwd() / f"{repo.name}-hid-{datetime.now():%Y%m%d-%H%M%S}.txt"

    labels = [label for _, label, _ in groups]
    setid = pack_set_id(labels, meta) if len(groups) > 1 else None
    outputs: dict[str, str] = {}
    for i, (name, label, es) in enumerate(groups, 1):
        pm: dict[str, object] = {**meta, "mode": mode}
        if setid:  # portion header so the unpacker can name/verify each part of the delivery
            pm.update({"pack-id": i, "pack-count": len(groups), "pack-label": label,
                       "pack-labels": ",".join(labels), "pack-set": setid})  # fmt: skip
        outputs[name] = make_payload(name or label, es, pm)
    if notes:
        log.info("framework-changes.txt: %d edited framework files, %s chars (also inside 2-code.txt)", len(modified), f"{len(notes):,}")
    if setid:
        log.info("pack-set %s has %d portions: %s", setid, len(groups),
                 ", ".join(f"{i}={label}" for i, label in enumerate(labels, 1)))  # fmt: skip

    typed = sum(len(t) for t in outputs.values())
    unpack_chars = len(Path(__file__).with_name("Unpack.java").read_bytes())
    log.info(
        "typing time at %.2f s/char: payload(s) ~%s, plus Unpack.java (%s chars) ~%s",
        SECONDS_PER_CHAR, _fmt_duration(typed * SECONDS_PER_CHAR),
        f"{unpack_chars:,}", _fmt_duration(unpack_chars * SECONDS_PER_CHAR),
    )  # fmt: skip
    if args.dry_run:
        log.info("dry run: nothing written")
        return 0
    if is_dir:
        target.mkdir(parents=True, exist_ok=True)
        for name, text in outputs.items():
            (target / name).write_text(text, encoding="ascii", newline="\n")
        if notes:
            (target / "framework-changes.txt").write_text(notes, encoding="utf-8", newline="\n")
        log.info("wrote %s: %s", target, ", ".join([*outputs, *(["framework-changes.txt"] if notes else [])]))
    else:
        target.write_text(outputs[""], encoding="ascii", newline="\n")
        log.info("wrote %s", target)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        return run(build_parser().parse_args(argv))
    except PackError as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
