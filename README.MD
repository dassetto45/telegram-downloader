# Telegram Media Downloader

**Pull every file out of a Telegram chat or channel — and download each one exactly once.**

Downloading a channel is easy. Downloading it *well* is not: the same file gets re-uploaded
under a dozen different message ids, a dropped connection leaves behind a file that looks
perfectly valid but is half empty, and running the script again starts the whole thing over.
This one handles all three, and it can clean up the library you already have.

* **Ask for what you want, not for everything.** Filter by text and by extension before a
  single byte moves. The search runs on Telegram's servers, so a 40.000 message channel is
  never pulled down just to find eleven pdfs. `--dry-run` shows you the matches and the total
  size first.
* **Never download the same file twice.** Re-uploads are matched by media id, or by name and
  exact size — so the copies you fetched months ago, before any of this existed, still count.
  No checksums, no re-reading files that live in the cloud.
* **An interrupted download can't lie to you.** Every file is written to a `.part` and gets
  its real name only once the size matches what Telegram declared. Network errors are retried,
  not fatal. `Ctrl-C` cleans up after itself.
* **Repair what is already broken.** `--find-duplicates` works entirely offline: it lists the
  redundant copies and the truncated files sitting in your download folder, tells you how many
  GB you would get back, and fixes them on request.

Pure Python and telethon, no database, no daemon, no config beyond a JSON file it writes for
you. Covered by 80 tests that need neither the network nor a Telegram account.

## Installing

```shell
pip3 install telethon requests

# optional, but downloads are noticeably faster with it:
# telethon offloads AES to a C extension instead of doing it in Python
pip3 install cryptg
```

`requests` is only needed if you turn on the notifications described at the bottom.

Developed and tested on Python 3.14 with telethon 1.42. Older Python is fine — the script installs
its own event loop, so it works on the 3.12+ versions where `telethon.sync` would otherwise fail with
`RuntimeError: There is no current event loop`.

## Running

```shell
python downloader.py
```

Get API hash and API id from https://my.telegram.org, under API Development and paste when prompted. 

If you want to recieve notifications when downloads are over, answer Y and read further

When prompted input the chat/channel from where you want to download all media and hit enter

## Filtering

Channels with mixed content don't have to be downloaded whole. Pass the filters on the command line:

```shell
# see what a query matches, without downloading anything
python downloader.py --channel "Nome Canale" --search "bilancio" --ext pdf --dry-run

# every pdf and every zip in the channel
python downloader.py --channel "Nome Canale" --ext pdf,zip
```

| Flag | Effect |
| --- | --- |
| `--channel NAME` | chat/channel to download from; skips the interactive list. Accepts the name or the number shown in that list |
| `--search TEXT` | only messages matching this text. Telegram runs the search server side, on captions and file names, so the whole history is never downloaded |
| `--ext LIST` | comma separated extensions, e.g. `pdf,zip`. A file is kept if its extension is **any** of them |
| `--dry-run` | print the matching files and the total size, download nothing |
| `--limit N` | stop after scanning N messages, handy to try out a query |
| `--allow-duplicates` | download a file even when the same name and size is already there |
| `--find-duplicates` | list the duplicate files already on disk and exit. Never connects to Telegram |
| `--delete` | with `--find-duplicates`, remove the extra copies and repair the truncated ones, after one confirmation |

Filters of different kinds combine with AND: `--search bilancio --ext pdf` keeps only pdf files
whose message matches *bilancio*.

Run the script with no arguments and it asks for chat, search text and extensions at the prompt;
leaving both filters empty downloads all media, exactly like before.

### Where files end up

```
<path>/<Channel>/<search-or-ext-slug>/   downloaded media
<path>/<Channel>/downloaded.json         which message ids were already fetched
```

Each search gets its own sub folder, while `downloaded.json` lives at channel level: a file
already fetched by one search is not downloaded again by another.

## Duplicates

Channels re-upload the same file over time, so different message ids end up carrying the same
attachment. Tracking message ids alone does not catch that. Two files are considered the same when
either check matches:

* **same media id** — `document.id` / `photo.id`. Identical means it is literally the same file on
  Telegram's servers, a forward or a repost. No false positives.
* **same normalized name and the exact same size in bytes** — this is what catches genuine
  re-uploads. Normalizing drops the ` (N)` suffix, collapses whitespace, lowercases, and folds the
  characters Windows and OneDrive forbid: a file sent as `Report 2024: Volume 1.pdf` is stored as
  `Report 2024_ Volume 1.pdf`, and the two must still match.

Content hashing is deliberately not used. It would require downloading the file first, and it does
not even work: two copies of the same pdf routinely differ by a handful of metadata bytes while being
the same document.

A duplicate is reported and skipped:

```
DUP [58] Report 2024: Extended Edition [Volume 5].pdf - already have it (Report 2024_ Extended Edition [Volume 5].pdf)
...
5 duplicate(s) skipped, 955.7 MB saved. Use --allow-duplicates to download them anyway.
```

The check runs during `--dry-run` too, and it also catches two copies inside the same scan. What is
already on disk counts: the whole channel folder is scanned, so files fetched before this feature
existed — and before `downloaded.json` recorded any metadata — are recognized.

Media with no file name (photos) are matched by media id only. Two different photos can weigh exactly
the same, and skipping one of them would be worse than keeping a duplicate.

### Cleaning up the duplicates you already have

```shell
# list them, delete nothing
python downloader.py --find-duplicates --channel "Nome Canale"

# same list, then remove the extra copies after one confirmation
python downloader.py --find-duplicates --channel "Nome Canale" --delete
```

This mode works entirely on the download folder: no API credentials, no connection to Telegram.
Without `--channel` it lists the channel folders it finds and asks which one. Of each group it keeps
the copy without the ` (N)` suffix, and when every copy is suffixed the oldest one:

```
Newsletter [Issue 290].pdf (6.2 MB) x2
    kept   08/05/2024  Newsletter [Issue 290].pdf
    extra  20/05/2024  Newsletter [Issue 290] (1).pdf

131 extra file(s) in 131 group(s), 661.0 MB recoverable.
```

Files whose size differs are never grouped as identical copies, even when the name matches: a
re-upload with a different size may well be a better scan. They get their own section, described
below.

## Interrupted downloads

An interrupted download used to leave a partial file that looked perfectly valid, and no trace in
`downloaded.json`. The next run could not recognize it — its size is wrong, so the duplicate check
misses it — and telethon parked a `nome (1).pdf` beside it. Two files, one broken, no way to tell
which.

Now every file is written to `<message id>.part` and renamed to its real name only after its size
matches what Telegram declared. **An incomplete file can no longer reach its final name.** A leftover
`.part` from a previous crash is cleared at the start of the next run, and `Ctrl-C` removes the one in
flight before exiting.

A network error no longer kills the run: the file is retried up to 3 times with a growing wait, then
reported and skipped, and the run moves on. Whatever was downloaded before the interruption keeps its
entry in `downloaded.json`.

### Recognizing a file that was already truncated

`telethon.utils.get_appropriated_part_size` returns 128, 256 or 512 KB depending on the file size, so
an interrupted download always ends on a chunk boundary. A file is treated as truncated when **both**
hold:

* it is smaller than the size Telegram declares for that message, and
* its size is an exact multiple of 128 KiB.

Both conditions are needed. "Smaller than expected" alone is not enough and would destroy data: a
re-upload can be a different, complete edition. In this library one channel holds a valid 3.0 MB pdf
whose message declares 4.7 MB — not a chunk multiple, correctly left alone. Across 5625 real files the
test flagged 3 files, all genuinely truncated, with no false positives.

A truncated file is redone in place, on its own name, with no ` (1)` copy — and it is checked before
`downloaded.json`, so a file that was recorded as downloaded but is broken on disk still gets fixed:

```
INCOMPLETE [1349] Newsletter [Issue 431].pdf 0.1/9.8 MB - downloading again
```

If a complete copy also exists beside the truncated one, nothing is downloaded — there is nothing to
repair, and `--find-duplicates` cleans up the leftover.

### Cleaning up truncated files you already have

`--find-duplicates` reports them in their own section:

```
Same name, different size (1 group(s)):

Bulletin [Volume 5].pdf
    truncated       55.8 MB  Bulletin [Volume 5].pdf
    complete       115.7 MB  Bulletin [Volume 5] (1).pdf
       -> would delete the truncated copy and rename the good one to Bulletin [Volume 5].pdf
```

With `--delete` it removes the truncated copy and renames the good one onto the clean name. It acts
only on groups with exactly one truncated file and at least one complete one; anything else prints
`different versions, left alone` and is never touched.

### Why no checksums anywhere

Every check in this script reads file sizes only, never file contents. On a OneDrive folder the files
are cloud placeholders: reading the tail of 40 of them took 389 seconds, because each read forces a
download. Verifying 5625 files would take about 15 hours and pull the whole library back down. Size
metadata is already local and answers the question well enough.

## Tests

```shell
python -m unittest test_downloader -v
```

Standard library only, no network and no Telegram client. Covers the duplicate detection and, through
a fake client, the download loop around it.

## Enable Notifications

It's possible to enable notification directly on your account.
In order to do so you have to:

1. Have a Telegram account using your phone number

2. Go into settings (via web or app) and set a username (if you already haven't)

This is needed to obtain an id which your bot will use to send messages to

3. Send a message to RawDataBot to get your id

Just search in app (or web) for **RawDataBot** and send any message (hi will do). Take a note of your id.

4. Create your bot

search for **BotFather** and send the message /start. Help is displayed. Send the message /newbot and follow the instructions. Take a note of your _token_ to access the HTTP API

Notifications are sent over HTTP, so `requests` has to be installed — see *Installing* above.
