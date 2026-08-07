# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/dassetto45/telegram-downloader/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/dassetto45/telegram-downloader/releases/tag/v1.0.0
