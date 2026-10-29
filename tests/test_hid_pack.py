"""End-to-end tests: hid_pack.py (Mac side) -> payload text -> Unpack.java (target side).

Run:  .venv/bin/python -m unittest discover -s tests -v
Needs git and a JDK (javac --release 8 keeps Unpack.java at the Java 8 API level).
"""
from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import hid_pack  # noqa: E402

PACK = ROOT / "hid_pack.py"
UNPACK_SRC = ROOT / "Unpack.java"
# What HIDcontroller/main.py:build_map() can type: printable ASCII, \n, \r, \t.
TYPEABLE = {chr(c) for c in range(0x20, 0x7F)} | {"\n", "\r", "\t"}
DATA_LINE = re.compile(r"^\d{4}[A-Za-z0-9+/=]+[0-9a-f]{8}$")

_classes: Path


def setUpModule() -> None:
    global _classes
    _classes = Path(tempfile.mkdtemp(prefix="hidpack-classes-"))
    r = subprocess.run(
        ["javac", "--release", "8", "-d", str(_classes), str(UNPACK_SRC)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("Unpack.java does not compile as Java 8:\n" + r.stderr)


def tearDownModule() -> None:
    shutil.rmtree(_classes, ignore_errors=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def git(repo: Path, *args: str) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    r = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def write(path: Path, text: str | bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    if mode is not None:
        path.chmod(mode)


def pack(repo: Path, out: Path | None, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(PACK), "--repo", str(repo), *args]
    if out is not None:
        cmd += ["-o", str(out)]
    return run(cmd)


def unpack(payload: Path, outdir: Path, *flags: str) -> subprocess.CompletedProcess:
    return run(["java", "-cp", str(_classes), "Unpack", str(payload), str(outdir), *flags])


def head_fields(path: Path) -> dict[str, str]:
    """Parse the `key: value` header of a payload document."""
    out: dict[str, str] = {}
    for ln in path.read_text().split("\n"):
        if ": " in ln and not DATA_LINE.match(ln):
            k, _, v = ln.partition(": ")
            out[k] = v
    return out


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hidpack-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo / ".gitignore", "ignored.txt\n")
        write(self.repo / "README.md", "hello\n")
        write(self.repo / "src/A.java", "class A {}\n")
        write(self.repo / "src/Gone.java", "class Gone {}\n")
        write(self.repo / "ui/zh.js", "export default { a: 1 }\n")
        write(self.repo / "run.sh", "#!/bin/sh\n", 0o755)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        # The deployed copy on the "target machine" = the base tree.
        self.target = self.tmp / "target"
        shutil.copytree(self.repo, self.target, ignore=shutil.ignore_patterns(".git"))
        self.payload = self.tmp / "payload.txt"

    def dirty(self) -> None:
        """Worktree changes on top of HEAD: Chinese edit, Chinese-named new file, delete, ignored."""
        write(self.repo / "ui/zh.js", "export default { a: '中文', b: '😀' }\n")
        write(self.repo / "docs/说明文档.md", "# 说明\n内容：你好，世界\n")
        write(self.repo / "src/B.java", "class B {}\n")
        write(self.repo / "new.sh", "#!/bin/sh\necho 你好\n", 0o755)
        write(self.repo / "ignored.txt", "nope\n")
        (self.repo / "src/Gone.java").unlink()

    def lines(self) -> list[str]:
        return self.payload.read_text().split("\n")

    def data_idx(self) -> list[int]:
        return [i for i, ln in enumerate(self.lines()) if DATA_LINE.match(ln)]


class RoundTrip(Base):
    def test_worktree_with_chinese_roundtrip(self) -> None:
        self.dirty()
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = unpack(self.payload, self.target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for rel in ["ui/zh.js", "docs/说明文档.md", "src/B.java", "new.sh"]:
            self.assertEqual((self.target / rel).read_bytes(), (self.repo / rel).read_bytes(), rel)
        self.assertTrue(os.access(self.target / "new.sh", os.X_OK))
        self.assertFalse((self.target / "ignored.txt").exists())
        # deletion is listed but not applied by default
        self.assertTrue((self.target / "src/Gone.java").exists())
        self.assertIn("src/Gone.java", r.stdout)
        # overwritten file keeps a backup next to it
        baks = list((self.target / "ui").glob("zh.js.bak.*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_text(), "export default { a: 1 }\n")

    def test_apply_deletes_backs_up(self) -> None:
        self.dirty()
        pack(self.repo, self.payload, "--from", "HEAD")
        r = unpack(self.payload, self.target, "--apply-deletes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.target / "src/Gone.java").exists())
        self.assertEqual(len(list((self.target / "src").glob("Gone.java.bak.*"))), 1)

    def test_no_backup_flag(self) -> None:
        self.dirty()
        pack(self.repo, self.payload, "--from", "HEAD")
        r = unpack(self.payload, self.target, "--no-backup", "--apply-deletes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(list(self.target.rglob("*.bak.*")), [])
        self.assertFalse((self.target / "src/Gone.java").exists())

    def test_unpack_dry_run_writes_nothing(self) -> None:
        self.dirty()
        pack(self.repo, self.payload, "--from", "HEAD")
        r = unpack(self.payload, self.target, "--dry-run", "--apply-deletes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.target / "src/B.java").exists())
        self.assertTrue((self.target / "src/Gone.java").exists())
        self.assertEqual((self.target / "ui/zh.js").read_text(), "export default { a: 1 }\n")

    def test_identical_file_is_not_rewritten_or_backed_up(self) -> None:
        write(self.repo / "src/A.java", "class A { int x; }\n")
        pack(self.repo, self.payload, "--from", "HEAD")
        write(self.target / "src/A.java", "class A { int x; }\n")
        r = unpack(self.payload, self.target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(list((self.target / "src").glob("A.java.bak.*")), [])

    def test_compiled_command_from_docs(self) -> None:
        # `cp Unpack.txt Unpack.java && javac Unpack.java && java Unpack payload outdir` is the documented flow,
        # and the only one that works on a JDK 8 target.
        self.dirty()
        pack(self.repo, self.payload, "--from", "HEAD")
        r = run(["java", "-cp", str(_classes), "Unpack", str(self.payload), str(self.target)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.target / "docs/说明文档.md").read_text(), "# 说明\n内容：你好，世界\n")

    def test_binary_and_multi_line_payload(self) -> None:
        blob = random.Random(42).randbytes(30_000) + b"\x00\xff\x00"
        write(self.repo / "assets/blob.bin", blob)
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(len(self.data_idx()), 100)
        r = unpack(self.payload, self.target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.target / "assets/blob.bin").read_bytes(), blob)


class Selection(Base):
    def test_ref_to_ref_reads_objects_not_worktree(self) -> None:
        write(self.repo / "src/A.java", "class A { int committed; }\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "c2")
        write(self.repo / "src/A.java", "class A { int dirty; }\n")  # uncommitted, must not ship
        write(self.repo / "src/Untracked.java", "class U {}\n")
        r = pack(self.repo, self.payload, "--from", "HEAD~1", "--to", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = unpack(self.payload, self.target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.target / "src/A.java").read_text(), "class A { int committed; }\n")
        self.assertFalse((self.target / "src/Untracked.java").exists())

    def test_full_tree_mode_respects_pathspec_and_gitignore(self) -> None:
        write(self.repo / "ignored.txt", "nope\n")
        out = self.tmp / "fresh"
        out.mkdir()
        r = pack(self.repo, self.payload, "src")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = unpack(self.payload, out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((out / "src/A.java").exists())
        self.assertFalse((out / "README.md").exists())
        self.assertFalse((out / "ignored.txt").exists())
        self.assertFalse((out / ".git").exists())

    def test_only_untypeable_keeps_just_files_hid_cannot_type(self) -> None:
        self.dirty()
        r = pack(self.repo, self.payload, "--from", "HEAD", "--only-untypeable")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = unpack(self.payload, self.target, "--apply-deletes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.target / "docs/说明文档.md").exists())
        self.assertTrue((self.target / "ui/zh.js").read_text().startswith("export default { a: '中文'"))
        self.assertFalse((self.target / "src/B.java").exists())  # plain ASCII: typed directly
        self.assertTrue((self.target / "src/Gone.java").exists())  # deletes are not part of this set

    def test_secret_looking_files_are_skipped(self) -> None:
        write(self.repo / ".env", "KEY=1\n")
        write(self.repo / "config/credentials.json", "{}\n")
        write(self.repo / "src/ok.pem", "x\n")
        write(self.repo / "src/C.java", "class C {}\n")
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(".env", r.stderr)
        out = self.tmp / "fresh"
        out.mkdir()
        unpack(self.payload, out)
        self.assertTrue((out / "src/C.java").exists())
        for rel in [".env", "config/credentials.json", "src/ok.pem"]:
            self.assertFalse((out / rel).exists(), rel)

    def test_pack_dry_run_lists_and_writes_nothing(self) -> None:
        self.dirty()
        r = pack(self.repo, None, "--from", "HEAD", "--dry-run", "-o", str(self.payload))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.payload.exists())
        self.assertIn("docs/说明文档.md", r.stdout + r.stderr)

    def test_nothing_to_pack_is_an_error(self) -> None:
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.payload.exists())

    def test_too_large_is_refused(self) -> None:
        # > MAX_LINES lines even at 256 base64 chars per line (~2.5M chars of payload)
        write(self.repo / "big.bin", random.Random(1).randbytes(3_000_000))
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("too large", r.stderr)


class Integrity(Base):
    def setUp(self) -> None:
        super().setUp()
        write(self.repo / "assets/blob.bin", random.Random(7).randbytes(6_000))
        write(self.repo / "ui/zh.js", "export default { a: '中文' }\n")
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.idx = self.data_idx()
        self.assertGreater(len(self.idx), 5)

    def assert_rejected(self, text: str, needle: str) -> None:
        bad = self.tmp / "bad.txt"
        bad.write_text(text)
        r = unpack(bad, self.target)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(needle, r.stdout + r.stderr)
        self.assertFalse((self.target / "ui/zh.js.bak").exists())
        self.assertFalse((self.target / "assets").exists(), "nothing may be written on a bad payload")

    def test_payload_is_typeable_and_ascii(self) -> None:
        text = self.payload.read_text()
        self.assertEqual(set(text) - TYPEABLE, set())

    def test_flipped_char_is_located(self) -> None:
        lines = self.lines()
        i = self.idx[2]
        ln = lines[i]
        lines[i] = ln[:10] + ("A" if ln[10] != "A" else "B") + ln[11:]
        self.assert_rejected("\n".join(lines), "0003")

    def test_missing_line_is_reported(self) -> None:
        lines = self.lines()
        del lines[self.idx[3]]
        self.assert_rejected("\n".join(lines), "0004")

    def test_truncated_payload_is_reported(self) -> None:
        lines = self.lines()
        cut = self.idx[-1]
        self.assert_rejected("\n".join(lines[:cut]), "END")

    def test_wrong_global_checksum_is_reported(self) -> None:
        text = re.sub(r"(?m)^sha256: [0-9a-f]{8}", "sha256: 00000000", self.payload.read_text())
        self.assert_rejected(text, "sha256")

    def test_tolerates_indent_crlf_blank_lines_and_surrounding_junk(self) -> None:
        mangled = ["$ cat > payload.txt <<'EOF'"]
        for ln in self.lines():
            mangled.append("   " + ln + "  ")
            mangled.append("")
        mangled.append("EOF")
        bad = self.tmp / "mangled.txt"
        bad.write_bytes("\r\n".join(mangled).encode())
        r = unpack(bad, self.target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.target / "ui/zh.js").read_text(), "export default { a: '中文' }\n")

    def test_unsafe_paths_are_rejected(self) -> None:
        absolute = str(self.tmp / "abs-evil.txt")
        for evil in ["../evil.txt", absolute, "a/../../evil.txt", "a\\b.txt", "C:/evil.txt", ""]:
            with self.subTest(path=evil):
                blob = hid_pack.pack_entries([hid_pack.Entry(hid_pack.FILE, evil, b"x")])
                p = self.tmp / "payload-evil.txt"
                p.write_text(hid_pack.armor(blob, {}))
                dest = self.tmp / "dest"
                dest.mkdir(exist_ok=True)
                r = unpack(p, dest)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertFalse((self.tmp / "evil.txt").exists())  # "../evil.txt" escaped dest
                self.assertFalse(Path(absolute).exists())
                self.assertEqual(list(dest.iterdir()), [])


class TxtTarget(Base):
    """The HID keyboard types into a .txt file (Notepad-style), so the unpacker must live with that."""

    def setUp(self) -> None:
        super().setUp()
        write(self.repo / "ui/zh.js", "export default { a: '中文' }\n")
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unpacker_runs_straight_from_a_txt_file(self) -> None:
        txt = self.tmp / "Unpack.txt"
        shutil.copy(UNPACK_SRC, txt)
        r = run(["java", "--source", "11", str(txt), str(self.payload), str(self.target)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.target / "ui/zh.js").read_text(), "export default { a: '中文' }\n")

    def _roundtrip_as(self, raw: bytes) -> None:
        saved = self.tmp / "saved-by-editor.txt"
        saved.write_bytes(raw)
        r = unpack(saved, self.target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.target / "ui/zh.js").read_text(), "export default { a: '中文' }\n")

    def test_notepad_unicode_utf16le_with_bom(self) -> None:
        self._roundtrip_as(b"\xff\xfe" + self.payload.read_text().replace("\n", "\r\n").encode("utf-16-le"))

    def test_utf16be_with_bom(self) -> None:
        self._roundtrip_as(b"\xfe\xff" + self.payload.read_text().encode("utf-16-be"))

    def test_utf8_with_bom_and_crlf(self) -> None:
        self._roundtrip_as(b"\xef\xbb\xbf" + self.payload.read_text().replace("\n", "\r\n").encode())

    def test_payload_default_name_is_txt(self) -> None:
        r = run([sys.executable, str(PACK), "--repo", str(self.repo), "--from", "HEAD"], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p.suffix for p in self.tmp.glob("repo-hid-*")], [".txt"])


class Init(Base):
    """--init: first delivery to a machine that has only the framework. New project files only."""

    def setUp(self) -> None:
        super().setUp()
        write(self.repo / "ui/zh.js", "export default { a: 1, menu: '菜单' }\n")  # framework file, edited
        (self.repo / "src/Gone.java").unlink()  # framework file, removed
        write(self.repo / "src/main/java/p/Mod.java", "// 中文注释\nclass Mod {}\n")
        write(self.repo / "ui/views/p/index.vue", "<template>你好</template>\n")
        write(self.repo / "src/main/resources/sql/mocks/seed.sql", "INSERT INTO t VALUES (1);\nGO\n")
        write(self.repo / "src/main/resources/sql/mock/tables.sql", "DROP TABLE t;\n")
        self.out = self.tmp / "out"

    def init(self, *extra: str) -> subprocess.CompletedProcess:
        return pack(self.repo, self.out, "--init", "--from", "HEAD", *extra)

    def test_writes_sql_code_and_framework_notes(self) -> None:
        r = self.init()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["1-sql.txt", "2-code.txt", "framework-changes.txt"])
        for p in self.out.glob("[12]-*.txt"):
            self.assertEqual(set(p.read_text()) - TYPEABLE, set(), p.name)

    def test_sql_document_holds_only_the_project_sql(self) -> None:
        self.init()
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        r = unpack(self.out / "1-sql.txt", fresh)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = sorted(str(p.relative_to(fresh)) for p in fresh.rglob("*") if p.is_file())
        self.assertEqual(files, ["src/main/resources/sql/mocks/seed.sql"])

    def test_code_document_has_new_files_and_notes_but_never_touches_the_framework(self) -> None:
        self.init()
        r = unpack(self.out / "2-code.txt", self.target, "--apply-deletes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for rel in ["src/main/java/p/Mod.java", "ui/views/p/index.vue"]:
            self.assertEqual((self.target / rel).read_bytes(), (self.repo / rel).read_bytes(), rel)
        self.assertEqual((self.target / "ui/zh.js").read_text(), "export default { a: 1 }\n")  # not overwritten
        self.assertTrue((self.target / "src/Gone.java").exists())  # not deleted
        self.assertFalse((self.target / "src/main/resources/sql").exists())  # sql travels in 1-sql.txt
        self.assertEqual(list(self.target.rglob("*.bak.*")), [])
        self.assertEqual(
            (self.target / "hid-notes/framework-changes.txt").read_bytes(),
            (self.out / "framework-changes.txt").read_bytes(),
        )

    def test_framework_notes_list_and_diff_the_edited_files(self) -> None:
        self.init()
        doc = (self.out / "framework-changes.txt").read_text(encoding="utf-8")
        self.assertIn("ui/zh.js", doc)
        self.assertIn("+export default { a: 1, menu: '菜单' }", doc)
        self.assertIn("Deleted since HEAD: 1 file", doc)
        self.assertNotIn("Mod.java", doc)

    def test_mock_sql_is_never_shipped_in_any_mode(self) -> None:
        r = self.init()
        self.assertIn("tables.sql", r.stderr)
        r = pack(self.repo, self.payload, "--from", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        unpack(self.payload, fresh)
        self.assertFalse((fresh / "src/main/resources/sql/mock").exists())
        self.assertTrue((fresh / "src/main/resources/sql/mocks/seed.sql").exists())

    def test_requires_from_and_rejects_to(self) -> None:
        r = pack(self.repo, self.out, "--init")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--from", r.stderr)
        r = self.init("--to", "HEAD")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.out.exists())

    def test_without_sql_there_is_no_sql_document(self) -> None:
        (self.repo / "src/main/resources/sql/mocks/seed.sql").unlink()
        r = self.init()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.out / "1-sql.txt").exists())
        self.assertTrue((self.out / "2-code.txt").exists())

    def test_without_edited_framework_files_there_are_no_notes(self) -> None:
        write(self.repo / "ui/zh.js", "export default { a: 1 }\n")  # back to the base content
        self.init()
        self.assertFalse((self.out / "framework-changes.txt").exists())
        unpack(self.out / "2-code.txt", self.target)
        self.assertFalse((self.target / "hid-notes").exists())

    def test_dry_run_writes_nothing(self) -> None:
        r = self.init("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.out.exists())
        self.assertIn("src/main/java/p/Mod.java", r.stdout)

    def test_nothing_new_is_an_error(self) -> None:
        clean = self.tmp / "clean"
        clean.mkdir()
        git(clean, "init", "-q", "-b", "main")
        write(clean / "a.txt", "x\n")
        git(clean, "add", "-A")
        git(clean, "commit", "-q", "-m", "base")
        r = pack(clean, self.tmp / "o", "--init", "--from", "HEAD")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing to pack", r.stderr)

    def test_sql_and_code_parts_form_a_checked_pack_set(self) -> None:
        self.init()
        sql, code = head_fields(self.out / "1-sql.txt"), head_fields(self.out / "2-code.txt")
        self.assertEqual((sql["pack-id"], code["pack-id"]), ("1", "2"))
        self.assertEqual((sql["pack-count"], code["pack-count"]), ("2", "2"))
        self.assertEqual(sql["pack-set"], code["pack-set"])
        self.assertEqual(sql["pack-labels"], "sql,code")
        r = unpack(self.out, self.target)  # the whole folder: both portions check out
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("complete (2/2)", r.stdout)
        self.assertTrue((self.target / "src/main/resources/sql/mocks/seed.sql").exists())

    def test_missing_sql_portion_is_named(self) -> None:
        self.init()
        (self.out / "1-sql.txt").unlink()
        r = unpack(self.out, self.target)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("INCOMPLETE", out)
        self.assertIn("1/2 (sql)", out)
        self.assertIn("MISSING", out)
        self.assertTrue((self.target / "src/main/java/p/Mod.java").exists())  # the good portion still applied


class Split(Base):
    """--split-by module: one numbered portion per top-level module, sharing a pack-set id."""

    def setUp(self) -> None:
        super().setUp()
        write(self.repo / "src/B.java", "class B {}\n")
        write(self.repo / "ui/zh.js", "export default { a: '中文' }\n")  # framework file, edited
        write(self.repo / "docs/readme.txt", "hi\n")
        self.out = self.tmp / "parts"
        r = pack(self.repo, self.out, "--from", "HEAD", "--split-by", "module")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_one_numbered_part_per_module(self) -> None:
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["1-docs.txt", "2-src.txt", "3-ui.txt"])
        for p in self.out.iterdir():
            self.assertEqual(set(p.read_text()) - TYPEABLE, set(), p.name)

    def test_parts_share_pack_set_id_count_and_labels(self) -> None:
        heads = [head_fields(self.out / n) for n in ["1-docs.txt", "2-src.txt", "3-ui.txt"]]
        self.assertEqual([h["pack-id"] for h in heads], ["1", "2", "3"])
        self.assertEqual({h["pack-count"] for h in heads}, {"3"})
        self.assertEqual(len({h["pack-set"] for h in heads}), 1)
        for h in heads:
            self.assertEqual(h["pack-labels"], "docs,src,ui")

    def test_directory_roundtrip_applies_every_portion(self) -> None:
        r = unpack(self.out, self.target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("complete (3/3)", r.stdout)
        for rel in ["src/B.java", "docs/readme.txt", "ui/zh.js"]:
            self.assertEqual((self.target / rel).read_bytes(), (self.repo / rel).read_bytes(), rel)

    def test_missing_portion_is_named_but_good_ones_apply(self) -> None:
        (self.out / "3-ui.txt").unlink()
        r = unpack(self.out, self.target)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("INCOMPLETE", out)
        self.assertIn("3/3 (ui)", out)
        self.assertIn("MISSING", out)
        self.assertIn("retype", out)
        self.assertTrue((self.target / "src/B.java").exists())
        self.assertTrue((self.target / "docs/readme.txt").exists())
        self.assertEqual((self.target / "ui/zh.js").read_text(), "export default { a: 1 }\n")  # portion never applied

    def test_failed_portion_is_named_but_good_ones_apply(self) -> None:
        part = self.out / "2-src.txt"
        lines = part.read_text().split("\n")
        i = next(i for i, ln in enumerate(lines) if DATA_LINE.match(ln))
        lines[i] = lines[i][:10] + ("A" if lines[i][10] != "A" else "B") + lines[i][11:]
        part.write_text("\n".join(lines))
        r = unpack(self.out, self.target)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("2/3 (src)", out)
        self.assertIn("FAILED", out)
        self.assertTrue((self.target / "docs/readme.txt").exists())
        self.assertTrue((self.target / "ui/zh.js").read_text().startswith("export default { a: '中文'"))
        self.assertFalse((self.target / "src/B.java").exists())

    def test_a_single_bad_portion_names_itself(self) -> None:
        part = self.out / "2-src.txt"
        lines = part.read_text().split("\n")
        i = next(i for i, ln in enumerate(lines) if DATA_LINE.match(ln))
        lines[i] = lines[i][:10] + ("A" if lines[i][10] != "A" else "B") + lines[i][11:]
        part.write_text("\n".join(lines))
        r = unpack(part, self.target)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("portion 2/3 (src)", out)
        self.assertIn("nothing written", out)

    def test_cli_dry_run_announces_the_set_and_writes_nothing(self) -> None:
        out2 = self.tmp / "dry"
        r = pack(self.repo, out2, "--from", "HEAD", "--split-by", "module", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(out2.exists())
        self.assertIn("pack-set", r.stderr)
        self.assertIn("2-src", r.stderr)


class Typeability(unittest.TestCase):
    def test_unpack_java_can_be_typed_by_hid(self) -> None:
        text = UNPACK_SRC.read_text()
        self.assertEqual(set(text) - TYPEABLE, set())
        self.assertNotIn("\t", text, "tab triggers shell completion when typed into a terminal")
        self.assertLess(len(text), 14_000, "keep the bootstrap cheap to type (~0.03 s per char)")

    def test_is_typeable_helper(self) -> None:
        self.assertTrue(hid_pack.is_typeable(b"abc\n\t {}~`"))
        self.assertFalse(hid_pack.is_typeable("中".encode()))
        self.assertFalse(hid_pack.is_typeable(b"\x00"))
        self.assertFalse(hid_pack.is_typeable(b"\x1b[0m"))


if __name__ == "__main__":
    unittest.main()
