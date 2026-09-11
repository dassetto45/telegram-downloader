# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **A search that matches nothing says so, and takes back the folder it had just
  created.** The destination is built before the first message is fetched, so a typo in
  `--search` or an `--ext` nobody ever uploaded used to leave an empty folder behind for
  good, and the run signed off with `Downloaded 0 new file(s)` pointing at it plus a
  notification about nothing. The run now reports what it looked for and found no match
  of, removes the search folder and, if it was created by the same run and is empty too,
  the channel folder above it, and sends no notification. Removal is `os.rmdir`, so a
  folder holding anything at all — a file, another search, the imported
  `downloaded.json` — is left alone by the operating system itself, and a folder that
  was already there before the run is never touched. Files that matched but failed to
  download also keep their folder: the retry needs it.

## [1.1.0] - 2026-08-07

### Added

- **Expired file references are refetched instead of retried blindly.** The token
  Telegram attaches to a media expires, and `iter_messages` hands out messages in
  batches of 100: the token for the hundredth file is minted alongside the first one
  and spent hours later. Telethon renews it on its own, but only for documents and
  only when the entity is cached — for photos the error reached us, and the retry loop
  then presented the very same dead token three times over. It now refetches the
  message with `get_messages` and retries with a fresh one, without the between-attempt
  wait, since this is not a network problem. A refetch returning nothing means the
  message was deleted, so the file is given up on immediately rather than burning the
  remaining attempts.

### Removed

- The *Known issues* section of the README. Both entries were stale: the event loop
  error is handled in the code by `ensure_event_loop()`, and the `UnsupportedMedia`
  workaround told users to install telethon 1.24, which would now downgrade a working
  install and break it on Python 3.12+ — and, as it turns out, pin them to the last
  telethon without the file reference renewal described above.

### Changed

- *Installing* lists every dependency in one place — `telethon`, `requests` for the
  notifications, and `cryptg` as an optional speedup — and states the versions the
  script is developed against.

## [1.0.0] - 2026-08-07

First tagged release. Everything before this point lives in the git history untagged.

### Added

- **Filters on the command line**: `--channel`, `--search`, `--ext`, `--limit` and
  `--dry-run`. The search runs server side on Telegram, so filtering a channel no
  longer means downloading its whole history. Filters of different kinds combine
  with AND. Run with no arguments and the script still asks everything at the prompt.
- **Duplicate detection**: a file already on disk is recognized by media id, or by
  normalized name plus exact size in bytes. The check runs during `--dry-run` too and
  catches duplicates inside the same scan. `--allow-duplicates` downloads them anyway.
- **`--find-duplicates`**: lists the duplicate files already in the download folder and
  exits. Works entirely offline, no API credentials needed. With `--delete` it removes
  the extra copies after one confirmation, keeping the copy without the ` (N)` suffix.
- **Truncated file repair**: a file smaller than declared *and* an exact multiple of
  128 KiB is treated as an interrupted download and fetched again in place. Both
  conditions are required, so a legitimately smaller re-upload is never destroyed.
  `--find-duplicates --delete` also repairs these in the existing library.
- **Per-search sub folders**: each search gets its own folder under the channel, while
  `downloaded.json` stays at channel level, so a file fetched by one search is not
  downloaded again by another.
- **Test suite** (`python -m unittest test_downloader -v`): standard library only, no
  network and no Telegram client. Covers duplicate detection and, through a fake
  client, the download loop around it.
- **`--version`** flag.

### Changed

- Downloads are written to `<message id>.part` and renamed to their final name only
  once the size matches what Telegram declared. An incomplete file can no longer reach
  its real name. A leftover `.part` is cleared on the next run, and `Ctrl-C` removes
  the one in flight.
- A network error no longer aborts the run: each file is retried up to 3 times with a
  growing wait, then reported and skipped.
- `downloaded.json` records file metadata, not just message ids. Entries written by
  earlier versions are still read.

### Fixed

- `RuntimeError: There is no current event loop in thread 'MainThread'` on Python 3.12+
  and 3.14, where the implicit loop creation `telethon.sync` relies on was removed.
- Older versions joined download path and channel name without a separator.

[Unreleased]: https://github.com/dassetto45/telegram-downloader/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/dassetto45/telegram-downloader/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dassetto45/telegram-downloader/releases/tag/v1.0.0
