# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

`python` is not on PATH here — use `python3` (3.14.4).

```shell
python3 -m unittest test_downloader -v     # full suite (91 tests, stdlib only, no network)
python3 test_downloader.py                 # same, but with buffer=True so passing tests stay quiet
python3 -m unittest test_downloader.DownloadChannelTest                       # one class
python3 -m unittest test_downloader.DownloadChannelTest.test_allow_duplicates_downloads_it_anyway

python3 downloader.py --help
python3 downloader.py --channel "Name" --search text --ext pdf --dry-run   # no network writes
python3 downloader.py --find-duplicates --channel "Name"                   # offline, no API creds
```

The test suite needs neither the network nor a Telegram account nor `config.json`; run it before
and after any change. Running `downloader.py` itself requires real API credentials, so exercising
the download path in a sandbox means going through `FakeClient` in the tests.

Dependencies are not pinned in a file: `pip3 install telethon requests`, optionally `cryptg`.

## Architecture

`downloader.py` is the whole program — one module, no packages, no database. Its shape matters more
than its size:

**Pure core, thin I/O shell.** Everything that decides *whether* a file should be downloaded is a
pure function of names, sizes and ids (`normalize_name`, `dup_key`, `looks_truncated`,
`matches_filters`, `plan_version_repair`, `resolve_target_path`). `download_channel` is the only
place that combines them with the network and the filesystem. New logic belongs in a pure function
with a unit test, not inside the loop.

**The duplicate index** (`build_duplicate_index`) is built once per run from two sources — the
`downloaded.json` tracking file *and* a walk of the whole channel folder — because histories written
by older versions hold only message ids, and files fetched before this feature existed exist only on
disk. It carries three maps, each answering a different question:

- `by_media` — media id → origin. Same `document.id`/`photo.id` means literally the same file.
- `by_key` — `"normalized name|size"` → origin. Catches genuine re-uploads. Size is part of the key.
- `by_name` — normalized name → `(path, largest size seen)`. Exists precisely because `by_key`
  cannot find a file whose size is *wrong*, which is the definition of a truncated download.

`remember_download` feeds the index back during the run so two copies inside the same scan are
caught, and it runs in `--dry-run` too.

**Order of checks in the loop is load-bearing.** Truncation is tested first: a tracking entry or a
dedup key would mask a broken file and it would stay broken forever. Then `known_ids`, then
duplicates.

**The `.part` protocol.** Downloads go to `<message id>.part` and are renamed with `os.replace` only
after the written size matches what Telegram declared, so an incomplete file can never reach its
real name. `clear_part_files` sweeps leftovers at the start of a run, `download_with_retries`
removes the in-flight one on `KeyboardInterrupt`, and the `finally` in `download_channel` flushes
tracking so a Ctrl-C never loses recorded downloads.

**Retries distinguish two failures.** Network errors: 3 attempts with a growing wait.
`FileReferenceExpiredError`: the message is refetched via `get_messages` (telethon only auto-renews
for documents with a cached entity) and retried immediately, with no wait, since it is not a network
problem — and a refetch returning `None` means the message is gone, so give up at once.

### Layout on disk

```
<path>/<Channel>/<search-or-ext-slug>/   media for this particular search
<path>/<Channel>/downloaded.json         tracking, shared by every search of the channel
config.json                              a LIST holding one dict — always config[0]['key']
sessions/test_session_101                telethon session (gitignored)
```

The per-search subfolder plus channel-level tracking is deliberate: a file fetched by one search is
not fetched again by another. `sanitize()` produces the channel folder name, and
`resolve_channel_dir` tries the raw name *before* the sanitized one, because existing folders may
contain characters `sanitize` would strip.

## Invariants

These are decisions with reasons behind them; do not "simplify" them away.

- **Only file sizes are ever read, never file contents.** No checksums anywhere. The target library
  lives on OneDrive where files are cloud placeholders and reading one forces a full download —
  measured at 389 s for 40 tail reads. Size metadata is local and good enough.
- **A file counts as truncated only if it is both smaller than declared and an exact multiple of
  128 KiB** (`CHUNK_SIZE`, telethon's minimum part size). Dropping the second condition would
  overwrite legitimately smaller re-uploads — data loss.
- **Media with no file name (photos) dedupe by media id only.** Two different photos can weigh
  exactly the same.
- **`normalize_name` folds the characters Windows/OneDrive forbid** (`FORBIDDEN_CHARS`), so
  `Report 2024: Volume 1.pdf` on Telegram matches `Report 2024_ Volume 1.pdf` on disk.
- **Tracking entries are read with `.get()`.** Entries written by pre-1.0 versions have only `id`.
- **Deletion never touches an ambiguous group.** `plan_version_repair` returns `None` unless there
  is exactly one truncated file and at least one complete one; `discard_empty_dirs` uses `os.rmdir`,
  which the OS itself refuses on a non-empty folder, and only on folders the run created.

## Conventions

- Comments inside the code are in Italian; docstrings, user-facing output and documentation are in
  English. Match whatever the surrounding lines do.
- The older helpers keep camelCase (`readFile`, `writeFile`, `buildEntry`, `sendNotification`);
  everything newer is snake_case. Do not rename them wholesale.
- Comments explain *why*, especially where a check looks redundant. That is what stops the next
  reader from removing a condition that prevents data loss.
- Tests exercise the real download loop through `FakeClient`/`fake_message` in `test_downloader.py`
  — `FakeClient.download_media` actually writes bytes, so the `.part` → rename path is covered.
  Subclass it (see `Expiring`, `Vanished`, `Interrupting`) for error behaviour.
- User-visible behaviour changes go in `CHANGELOG.md` under `[Unreleased]`, as a prose paragraph
  that states the problem and the reasoning, matching the existing entries. Bump `__version__` in
  `downloader.py` when releasing, and update `README.MD` (note its filename is uppercase, and its
  test count is currently stale at 80).
