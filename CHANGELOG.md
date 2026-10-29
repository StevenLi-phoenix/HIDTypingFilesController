# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed
- Payload lines carry 256 base64 characters (was 100): fewer lines and checksums to type; the 9999-line limit now covers
  ~2.5M characters. `Unpack` is unchanged and still reads older payloads.
- Docs rewritten: Chinese `README.md` (with a flow diagram) and `pi-standalone/README.md`; every example uses the Java 8
  compile-then-run flow (`javac Unpack.java && java Unpack ...`) and generic repo/tag names.
- The end-to-end test runs the compiled `Unpack` class instead of the JDK 11+ source launcher.

## [0.3.0] - 2026-10-09

### Added
- `--split-by module`: pack one numbered `.txt` portion per top-level module (root files -> `root`), so a portion that fails to
  type is retyped alone instead of the whole run. `--init --split-by module` keeps `1-sql.txt` first and splits the code part by
  module. Portions are ordered and named `1-src.txt`, `2-ui.txt`, ...
- Pack-set metadata in the payload header (`pack-id`, `pack-count`, `pack-label`, `pack-labels`, `pack-set`) shared by every
  portion of one delivery. `--init`'s `1-sql.txt` / `2-code.txt` now form such a set.
- `Unpack` accepts a **directory** of portions: it applies every good portion, then prints a per-portion
  `OK`/`FAILED`/`MISSING` report and names exactly which portion to retype (exit 2 if any is missing/failed), while a single
  payload still writes nothing on failure. A lone failed portion also names itself (`portion 2/3 (src) ...`).

## [0.2.0] - 2026-10-09

### Added
- `--init`: first delivery onto a machine that already has the framework. Packs only NEW files, split into `1-sql.txt` (project
  `*.sql`, run first) and `2-code.txt`; edited framework files and deletions are not packed but written up in
  `framework-changes.txt` (list + diffs, UTF-8), which also travels inside `2-code.txt` as `hid-notes/framework-changes.txt`.
- `Unpack` runs straight from a `.txt` (`java --source 11 Unpack.txt payload.txt .`) and reads payloads saved by Notepad as
  UTF-8 with BOM or UTF-16 LE/BE.

### Changed
- `*.sql` under a `mock/` directory is never packed (in every mode): it is local-dev setup that may `DROP` shared tables.
- Docs and examples use the `.txt` flow; stats are reported per document with a combined typing-time estimate.

## [0.1.0] - 2026-10-09

### Added
- `hid_pack.py`: packs a git repo's files (Chinese, binary, executable bits, deletions) into one HID-typeable ASCII payload
  (gzip + base64, numbered lines with CRC32, SHA-256 header). Selection by `--from`/`--to` refs or the working tree
  (with untracked files), pathspecs, `--only-untypeable`; skips secret-looking files; reports size and typing time.
- `Unpack.java`: Java 8 single-file unpacker. Verifies every line and the whole payload before writing anything,
  refuses unsafe paths, backs up overwritten files, `--dry-run`, `--no-backup`, `--apply-deletes`.
- 23 end-to-end tests (pack -> unpack, corruption, truncation, whitespace mangling, path traversal, Java 8 API level).
