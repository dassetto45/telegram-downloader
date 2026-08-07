# -*- coding: utf-8 -*-
"""Test di deduplica e file interrotti. Nessuna rete, nessun client Telegram.

    python -m unittest test_downloader -v
"""
import datetime
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import downloader
from downloader import FileReferenceExpiredError


def touch(path, size, mtime=None):
    """Crea un file di `size` byte, opzionalmente con un mtime fissato."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class NormalizeNameTest(unittest.TestCase):
    def test_strips_the_duplicate_suffix(self):
        self.assertEqual(
            downloader.normalize_name("Newsletter [Issue 290] (1).pdf"),
            "newsletter [issue 290].pdf")

    def test_strips_multi_digit_suffixes(self):
        self.assertEqual(downloader.normalize_name("file (12).cbz"), "file.cbz")

    def test_lowercases_name_and_extension(self):
        self.assertEqual(downloader.normalize_name("Issue 365 [Archive].PDF"),
                         "issue 365 [archive].pdf")

    def test_collapses_repeated_whitespace(self):
        self.assertEqual(downloader.normalize_name("file  con   spazi (3).cbz"),
                         "file con spazi.cbz")

    def test_keeps_names_without_extension(self):
        self.assertEqual(downloader.normalize_name("senza estensione"), "senza estensione")

    def test_characters_windows_forbids_are_folded(self):
        """Su disco 'Report 2024: ...' diventa 'Report 2024_ ...': Windows e OneDrive
        non ammettono i due punti, quindi i due nomi devono coincidere."""
        self.assertEqual(
            downloader.normalize_name("Report 2024: Extended Edition [Volume 1].pdf"),
            downloader.normalize_name("Report 2024_ Extended Edition [Volume 1].pdf"))

    def test_every_forbidden_character_is_folded(self):
        for forbidden in '\\/:*?"<>|':
            with self.subTest(char=forbidden):
                self.assertEqual(downloader.normalize_name(f"a{forbidden}b.pdf"),
                                 downloader.normalize_name("a_b.pdf"))

    def test_a_number_in_the_middle_is_not_a_suffix(self):
        self.assertEqual(downloader.normalize_name("Dylan Dog (011) Diabolo.cbr"),
                         "dylan dog (011) diabolo.cbr")

    def test_missing_or_empty_names(self):
        for name in (None, "", "   ", "(1).pdf"):
            with self.subTest(name=name):
                self.assertIsNone(downloader.normalize_name(name))


class DupKeyTest(unittest.TestCase):
    def test_same_file_with_and_without_suffix_shares_the_key(self):
        self.assertEqual(downloader.dup_key("a.pdf", 6459251),
                         downloader.dup_key("a (1).pdf", 6459251))

    def test_different_size_means_different_key(self):
        self.assertNotEqual(downloader.dup_key("a.pdf", 100),
                            downloader.dup_key("a.pdf", 101))

    def test_different_name_means_different_key(self):
        self.assertNotEqual(downloader.dup_key("a.pdf", 100),
                            downloader.dup_key("b.pdf", 100))

    def test_unusable_metadata_yields_no_key(self):
        for name, size in ((None, 100), ("a.pdf", None), ("a.pdf", 0), (None, None)):
            with self.subTest(name=name, size=size):
                self.assertIsNone(downloader.dup_key(name, size))


class MediaIdTest(unittest.TestCase):
    def test_document_id(self):
        message = SimpleNamespace(document=SimpleNamespace(id=42), photo=None)
        self.assertEqual(downloader.media_id(message), 42)

    def test_photo_id(self):
        message = SimpleNamespace(document=None, photo=SimpleNamespace(id=7))
        self.assertEqual(downloader.media_id(message), 7)

    def test_no_media(self):
        self.assertIsNone(downloader.media_id(SimpleNamespace(document=None, photo=None)))
        self.assertIsNone(downloader.media_id(SimpleNamespace()))


class BuildDuplicateIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_legacy_tracking_without_metadata_is_ignored(self):
        tracking = [{"id": 1, "date_time": "2022-09-02 16:20:50", "completed": True}]
        index = downloader.build_duplicate_index(self.dir, tracking)
        self.assertEqual(index["by_key"], {})
        self.assertEqual(index["by_media"], {})

    def test_new_tracking_entries_are_indexed(self):
        tracking = [{"id": 5, "name": "a.pdf", "size": 100, "media_id": 77}]
        index = downloader.build_duplicate_index(self.dir, tracking)
        self.assertEqual(index["by_key"][downloader.dup_key("a.pdf", 100)], "msg 5")
        self.assertEqual(index["by_media"][77], "msg 5")

    def test_files_on_disk_are_indexed_with_their_relative_path(self):
        touch(os.path.join(self.dir, "ext_pdf", "a.pdf"), 100)
        index = downloader.build_duplicate_index(self.dir, [])
        self.assertEqual(index["by_key"][downloader.dup_key("a.pdf", 100)],
                         os.path.join("ext_pdf", "a.pdf"))

    def test_a_file_already_on_disk_matches_the_message_that_would_re_upload_it(self):
        touch(os.path.join(self.dir, "Issue 290 (1).pdf"), 6459251)
        index = downloader.build_duplicate_index(self.dir, [])
        self.assertIn(downloader.dup_key("Issue 290.pdf", 6459251), index["by_key"])

    def test_the_tracking_file_itself_is_not_indexed(self):
        with open(os.path.join(self.dir, downloader.TRACKING_FILENAME), "w") as fp:
            json.dump([], fp)
        index = downloader.build_duplicate_index(self.dir, [])
        self.assertEqual(index["by_key"], {})

    def test_tracking_wins_over_disk_for_the_same_key(self):
        touch(os.path.join(self.dir, "a.pdf"), 100)
        tracking = [{"id": 5, "name": "a.pdf", "size": 100}]
        index = downloader.build_duplicate_index(self.dir, tracking)
        self.assertEqual(index["by_key"][downloader.dup_key("a.pdf", 100)], "msg 5")

    def test_missing_directory_is_not_an_error(self):
        index = downloader.build_duplicate_index(os.path.join(self.dir, "nope"), [])
        self.assertEqual(index["by_key"], {})


class GroupDuplicatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_same_name_and_size_form_a_group_with_the_original_first(self):
        original = touch(os.path.join(self.dir, "a.pdf"), 100, mtime=1_700_000_000)
        copy = touch(os.path.join(self.dir, "a (1).pdf"), 100, mtime=1_800_000_000)
        groups = downloader.group_duplicates(self.dir)
        self.assertEqual(groups, [[original, copy]])

    def test_same_name_but_different_size_is_not_a_duplicate(self):
        touch(os.path.join(self.dir, "b.pdf"), 100)
        touch(os.path.join(self.dir, "b (1).pdf"), 200)
        self.assertEqual(downloader.group_duplicates(self.dir), [])

    def test_a_lone_file_is_not_a_group(self):
        touch(os.path.join(self.dir, "c.pdf"), 100)
        self.assertEqual(downloader.group_duplicates(self.dir), [])

    def test_when_every_copy_is_suffixed_the_oldest_is_kept(self):
        newer = touch(os.path.join(self.dir, "d (2).pdf"), 100, mtime=1_800_000_000)
        older = touch(os.path.join(self.dir, "d (1).pdf"), 100, mtime=1_700_000_000)
        groups = downloader.group_duplicates(self.dir)
        self.assertEqual(groups, [[older, newer]])

    def test_duplicates_are_found_across_subfolders(self):
        first = touch(os.path.join(self.dir, "all", "e.pdf"), 100, mtime=1_700_000_000)
        second = touch(os.path.join(self.dir, "ext_pdf", "e.pdf"), 100, mtime=1_800_000_000)
        self.assertEqual(downloader.group_duplicates(self.dir), [[first, second]])

    def test_the_tracking_file_is_never_grouped(self):
        for folder in ("all", "ext_pdf"):
            path = os.path.join(self.dir, folder, downloader.TRACKING_FILENAME)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fp:
                json.dump([], fp)
        self.assertEqual(downloader.group_duplicates(self.dir), [])


class FakeClient:
    """iter_messages/download_media quanto basta a far girare download_channel.

    download_media scrive davvero il file al percorso ricevuto, cosi' il giro
    '.part' -> rename viene esercitato. `failures` fa fallire i primi N tentativi.
    """

    def __init__(self, messages, failures=0, error=None, written_size=None):
        self.messages = messages
        self.downloaded = []
        self.attempts = 0
        self.failures = failures
        self.error = error or IOError("boom")
        self.written_size = written_size

    def iter_messages(self, entity, search=None, limit=None):
        return iter(self.messages)

    def download_media(self, message, path, progress_callback=None):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.error
        size = message.file.size if self.written_size is None else self.written_size
        with open(path, "wb") as fp:
            fp.write(b"\0" * size)
        self.downloaded.append(message.id)
        if progress_callback:
            progress_callback(size, size)
        return path


def fake_message(message_id, name, size, document_id=None, photo=False):
    media = SimpleNamespace(id=document_id if document_id is not None else message_id)
    ext = os.path.splitext(name or "")[1] or (".jpg" if photo else None)
    return SimpleNamespace(
        id=message_id,
        media=media,
        document=None if photo else media,
        photo=media if photo else None,
        date=datetime.datetime(2025, 1, 13, 14, 30, 44),
        file=SimpleNamespace(name=name, size=size, ext=ext))


class DownloadChannelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.channel_dir = self.tmp.name
        self.target_dir = os.path.join(self.channel_dir, "all")
        os.makedirs(self.target_dir)
        self.addCleanup(self.tmp.cleanup)

    def run_download(self, client, allow_duplicates=False, dry_run=False):
        return downloader.download_channel(
            client, entity=None, channel_dir=self.channel_dir,
            target_dir=self.target_dir, search=None, extensions=set(),
            dry_run=dry_run, limit=None, allow_duplicates=allow_duplicates)

    def write_tracking(self, entries):
        downloader.writeFile(entries, self.channel_dir, downloader.TRACKING_FILENAME)

    def test_a_file_already_on_disk_is_not_downloaded_again(self):
        touch(os.path.join(self.target_dir, "a.pdf"), 100)
        client = FakeClient([fake_message(1, "a.pdf", 100)])
        summary = self.run_download(client)
        self.assertEqual(client.downloaded, [])
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(summary["duplicate_bytes"], 100)
        self.assertEqual(summary["downloaded"], 0)

    def test_allow_duplicates_downloads_it_anyway(self):
        touch(os.path.join(self.target_dir, "a.pdf"), 100)
        client = FakeClient([fake_message(1, "a.pdf", 100)])
        summary = self.run_download(client, allow_duplicates=True)
        self.assertEqual(client.downloaded, [1])
        self.assertEqual(summary["duplicates"], 0)

    def test_the_windows_substitution_does_not_hide_the_duplicate(self):
        touch(os.path.join(self.target_dir, "Report 2024_ Volume 1.pdf"), 100)
        client = FakeClient([fake_message(1, "Report 2024: Volume 1.pdf", 100)])
        self.assertEqual(self.run_download(client)["duplicates"], 1)

    def test_two_copies_in_the_same_scan(self):
        client = FakeClient([fake_message(1, "a.pdf", 100),
                             fake_message(2, "a (1).pdf", 100)])
        summary = self.run_download(client)
        self.assertEqual(client.downloaded, [1])
        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(summary["duplicates"], 1)

    def test_the_same_media_id_is_a_duplicate_even_under_another_name(self):
        client = FakeClient([fake_message(1, "a.pdf", 100, document_id=99),
                             fake_message(2, "b.pdf", 200, document_id=99)])
        summary = self.run_download(client)
        self.assertEqual(client.downloaded, [1])
        self.assertEqual(summary["duplicates"], 1)

    def test_an_already_tracked_message_counts_as_skipped_not_duplicate(self):
        self.write_tracking([downloader.buildEntry(1, "a.pdf", 100, 99)])
        client = FakeClient([fake_message(1, "a.pdf", 100, document_id=99)])
        summary = self.run_download(client)
        self.assertEqual(client.downloaded, [])
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["duplicates"], 0)

    def test_nameless_media_of_the_same_size_are_both_downloaded(self):
        client = FakeClient([fake_message(1, None, 100, document_id=11),
                             fake_message(2, None, 100, document_id=22)])
        summary = self.run_download(client)
        self.assertEqual(client.downloaded, [1, 2])
        self.assertEqual(summary["duplicates"], 0)

    def test_the_metadata_lands_in_the_tracking_file(self):
        client = FakeClient([fake_message(7, "a.pdf", 100, document_id=99)])
        self.run_download(client)
        entries = downloader.readFile(self.channel_dir, downloader.TRACKING_FILENAME)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], 7)
        self.assertEqual(entries[0]["name"], "a.pdf")
        self.assertEqual(entries[0]["size"], 100)
        self.assertEqual(entries[0]["media_id"], 99)

    def test_a_legacy_tracking_file_still_works(self):
        self.write_tracking([{"id": 1, "date_time": "2022-09-02 16:20:50",
                              "completed": True}])
        client = FakeClient([fake_message(1, "a.pdf", 100),
                             fake_message(2, "b.pdf", 200)])
        summary = self.run_download(client)
        self.assertEqual(client.downloaded, [2])
        self.assertEqual(summary["skipped"], 1)

    def test_no_part_file_survives_a_normal_download(self):
        client = FakeClient([fake_message(1, "a.pdf", 100)])
        self.run_download(client)
        self.assertEqual(sorted(os.listdir(self.target_dir)), ["a.pdf"])
        self.assertEqual(os.path.getsize(os.path.join(self.target_dir, "a.pdf")), 100)

    def test_a_truncated_file_is_replaced_in_place(self):
        expected = downloader.CHUNK_SIZE * 4
        broken = touch(os.path.join(self.target_dir, "a.pdf"), downloader.CHUNK_SIZE)
        client = FakeClient([fake_message(1, "a.pdf", expected)])
        summary = self.run_download(client)
        self.assertEqual(sorted(os.listdir(self.target_dir)), ["a.pdf"])
        self.assertEqual(os.path.getsize(broken), expected)
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(summary["duplicates"], 0)

    def test_a_truncated_file_is_redone_even_if_the_message_is_already_tracked(self):
        expected = downloader.CHUNK_SIZE * 4
        broken = touch(os.path.join(self.target_dir, "a.pdf"), downloader.CHUNK_SIZE)
        self.write_tracking([downloader.buildEntry(1, "a.pdf", expected, 99)])
        client = FakeClient([fake_message(1, "a.pdf", expected, document_id=99)])
        summary = self.run_download(client)
        self.assertEqual(os.path.getsize(broken), expected)
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(summary["skipped"], 0)

    def test_a_complete_file_of_another_size_is_not_overwritten(self):
        """Il caso Jujutsu: edizione diversa, valida. Deve restare intatta."""
        existing = touch(os.path.join(self.target_dir, "a.pdf"), 3168410)
        client = FakeClient([fake_message(1, "a.pdf", 4949195)])
        summary = self.run_download(client)
        self.assertEqual(os.path.getsize(existing), 3168410)
        self.assertEqual(os.path.getsize(os.path.join(self.target_dir, "a (1).pdf")), 4949195)
        self.assertEqual(summary["incomplete"], 0)

    def test_a_short_download_is_never_renamed_into_place(self):
        """Il client scrive meno del dovuto: il file finale non deve nascere."""
        client = FakeClient([fake_message(1, "a.pdf", 1000)], written_size=400)
        with mock.patch.object(downloader, "RETRY_WAIT_SECONDS", 0):
            summary = self.run_download(client)
        self.assertEqual(os.listdir(self.target_dir), [])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["downloaded"], 0)

    def test_it_retries_and_then_succeeds(self):
        client = FakeClient([fake_message(1, "a.pdf", 100)], failures=2)
        with mock.patch.object(downloader, "RETRY_WAIT_SECONDS", 0):
            summary = self.run_download(client)
        self.assertEqual(client.attempts, 3)
        self.assertEqual(sorted(os.listdir(self.target_dir)), ["a.pdf"])
        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(summary["failed"], 0)

    def test_a_file_that_keeps_failing_does_not_stop_the_run(self):
        client = FakeClient([fake_message(1, "a.pdf", 100),
                             fake_message(2, "b.pdf", 200)],
                            failures=downloader.DOWNLOAD_ATTEMPTS)
        with mock.patch.object(downloader, "RETRY_WAIT_SECONDS", 0):
            summary = self.run_download(client)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(sorted(os.listdir(self.target_dir)), ["b.pdf"])

    def test_a_failed_file_is_not_written_to_the_tracking_file(self):
        client = FakeClient([fake_message(1, "a.pdf", 100)],
                            failures=downloader.DOWNLOAD_ATTEMPTS)
        with mock.patch.object(downloader, "RETRY_WAIT_SECONDS", 0):
            self.run_download(client)
        self.assertEqual(downloader.readFile(self.channel_dir,
                                            downloader.TRACKING_FILENAME), [])

    def test_an_expired_file_reference_is_refetched_before_retrying(self):
        """Il file_reference scade: telethon lo rinnova da solo sui documenti, ma su
        foto e entita' non in cache l'errore arriva fin qui. Riprovare con lo stesso
        oggetto message ripresenta il token gia' morto, quindi va ripescato."""
        fresh = fake_message(1, "a.pdf", 100)

        class Expiring(FakeClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.refetched = []

            def get_messages(self, entity, ids=None):
                self.refetched.append(ids)
                return fresh

            def download_media(self, message, path, progress_callback=None):
                # solo il messaggio ripescato porta un token valido
                if message is not fresh:
                    self.attempts += 1
                    raise FileReferenceExpiredError(request=None)
                return super().download_media(message, path, progress_callback)

        client = Expiring([fake_message(1, "a.pdf", 100)])
        with mock.patch.object(downloader, "RETRY_WAIT_SECONDS", 0):
            summary = self.run_download(client)

        self.assertEqual(client.refetched, [1])
        self.assertEqual(sorted(os.listdir(self.target_dir)), ["a.pdf"])
        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(summary["failed"], 0)

    def test_a_deleted_message_is_given_up_on_without_burning_the_retries(self):
        """Se il refetch torna None il messaggio non esiste piu': insistere e' inutile."""
        class Vanished(FakeClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.refetched = []

            def get_messages(self, entity, ids=None):
                self.refetched.append(ids)
                return None

            def download_media(self, message, path, progress_callback=None):
                self.attempts += 1
                raise FileReferenceExpiredError(request=None)

        client = Vanished([fake_message(1, "a.pdf", 100)])
        with mock.patch.object(downloader, "RETRY_WAIT_SECONDS", 0):
            summary = self.run_download(client)

        self.assertEqual(client.attempts, 1)
        self.assertEqual(client.refetched, [1])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(os.listdir(self.target_dir), [])

    def test_ctrl_c_cleans_up_and_propagates(self):
        client = FakeClient([fake_message(1, "a.pdf", 100)],
                            failures=1, error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_download(client)
        self.assertEqual(os.listdir(self.target_dir), [])

    def test_ctrl_c_keeps_the_tracking_of_what_was_already_downloaded(self):
        """Il flush avviene ogni FLUSH_EVERY: senza il finally, i file scaricati prima
        dell'interruzione perderebbero la loro voce in downloaded.json."""
        class Interrupting(FakeClient):
            def download_media(self, message, path, progress_callback=None):
                if message.id == 2:
                    raise KeyboardInterrupt()
                return super().download_media(message, path, progress_callback)

        client = Interrupting([fake_message(1, "a.pdf", 100),
                               fake_message(2, "b.pdf", 200)])
        with self.assertRaises(KeyboardInterrupt):
            self.run_download(client)
        entries = downloader.readFile(self.channel_dir, downloader.TRACKING_FILENAME)
        self.assertEqual([entry["id"] for entry in entries], [1])
        self.assertEqual(sorted(os.listdir(self.target_dir)), ["a.pdf"])

    def test_a_leftover_part_file_is_cleared_before_starting(self):
        touch(os.path.join(self.target_dir, "99.part"), 500)
        client = FakeClient([fake_message(1, "a.pdf", 100)])
        self.run_download(client)
        self.assertEqual(sorted(os.listdir(self.target_dir)), ["a.pdf"])

    def test_dry_run_reports_a_truncated_file_without_touching_it(self):
        broken = touch(os.path.join(self.target_dir, "a.pdf"), downloader.CHUNK_SIZE)
        client = FakeClient([fake_message(1, "a.pdf", downloader.CHUNK_SIZE * 4)])
        summary = self.run_download(client, dry_run=True)
        self.assertEqual(client.downloaded, [])
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(os.path.getsize(broken), downloader.CHUNK_SIZE)

    def test_dry_run_reports_duplicates_without_downloading(self):
        touch(os.path.join(self.target_dir, "a.pdf"), 100)
        client = FakeClient([fake_message(1, "a.pdf", 100),
                             fake_message(2, "b.pdf", 200),
                             fake_message(3, "b.pdf", 200)])
        summary = self.run_download(client, dry_run=True)
        self.assertEqual(client.downloaded, [])
        self.assertEqual(summary["duplicates"], 2)
        self.assertEqual(downloader.readFile(self.channel_dir,
                                            downloader.TRACKING_FILENAME), [])


class LooksTruncatedTest(unittest.TestCase):
    def test_the_real_cases_from_the_library(self):
        # Bulletin [Volume 5]: 446 chunk esatti contro i 115.7 MB attesi
        self.assertTrue(downloader.looks_truncated(58458112, 121307231))
        # Space Chef Caisar: 2 chunk esatti contro 211 MB
        self.assertTrue(downloader.looks_truncated(262144, 221825076))
        # Dragonero 016: 684 chunk esatti
        self.assertTrue(downloader.looks_truncated(89653248, 148480792))

    def test_a_smaller_but_complete_file_is_not_truncated(self):
        """Jujutsu Kaisen 129: pdf valido di un'edizione diversa, piu' piccolo del
        messaggio ma non su un confine di chunk. Sovrascriverlo sarebbe una perdita."""
        self.assertFalse(downloader.looks_truncated(3168410, 4949195))
        self.assertFalse(downloader.looks_truncated(3567784, 5716870))

    def test_a_chunk_multiple_that_is_not_smaller_is_not_truncated(self):
        self.assertFalse(downloader.looks_truncated(262144, 262144))
        self.assertFalse(downloader.looks_truncated(524288, 262144))

    def test_unusable_sizes(self):
        for size, expected in ((0, 100), (100, 0), (None, 100), (100, None)):
            with self.subTest(size=size, expected=expected):
                self.assertFalse(downloader.looks_truncated(size, expected))


class NextFreeNameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_first_free_index(self):
        touch(os.path.join(self.dir, "a.pdf"), 10)
        self.assertEqual(downloader.next_free_name(os.path.join(self.dir, "a.pdf")),
                         os.path.join(self.dir, "a (1).pdf"))

    def test_skips_the_indexes_already_taken(self):
        for name in ("a.pdf", "a (1).pdf", "a (2).pdf"):
            touch(os.path.join(self.dir, name), 10)
        self.assertEqual(downloader.next_free_name(os.path.join(self.dir, "a.pdf")),
                         os.path.join(self.dir, "a (3).pdf"))


class ResolveTargetPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_a_free_name_is_used_as_is(self):
        self.assertEqual(downloader.resolve_target_path(self.dir, "a.pdf", 1000),
                         os.path.join(self.dir, "a.pdf"))

    def test_a_truncated_file_is_replaced_in_place(self):
        touch(os.path.join(self.dir, "a.pdf"), downloader.CHUNK_SIZE)
        self.assertEqual(downloader.resolve_target_path(self.dir, "a.pdf", 999999),
                         os.path.join(self.dir, "a.pdf"))

    def test_a_complete_file_of_another_size_is_left_alone(self):
        existing = touch(os.path.join(self.dir, "a.pdf"), 3168410)
        self.assertEqual(downloader.resolve_target_path(self.dir, "a.pdf", 4949195),
                         os.path.join(self.dir, "a (1).pdf"))
        self.assertEqual(os.path.getsize(existing), 3168410)

    def test_a_truncated_file_in_another_folder_is_replaced_where_it_is(self):
        """Il tronco puo' stare in una sottocartella di un'altra search: va sostituito
        li', non affiancato da una copia nuova nella cartella corrente."""
        elsewhere = touch(os.path.join(self.dir, "all", "a.pdf"), downloader.CHUNK_SIZE)
        target = os.path.join(self.dir, "ext_pdf")
        os.makedirs(target)
        self.assertEqual(
            downloader.resolve_target_path(target, "a.pdf", 999999, elsewhere),
            elsewhere)


class TargetNameTest(unittest.TestCase):
    def test_the_file_name_when_there_is_one(self):
        message = fake_message(1, "a.pdf", 100)
        self.assertEqual(downloader.target_name(message, message.file), "a.pdf")

    def test_nameless_media_get_telethon_s_own_format(self):
        message = fake_message(1, None, 100, photo=True)
        self.assertEqual(downloader.target_name(message, message.file),
                         "photo_2025-01-13_14-30-44.jpg")

    def test_a_nameless_document_is_called_document(self):
        message = fake_message(1, None, 100)
        message.file.ext = ".bin"
        self.assertEqual(downloader.target_name(message, message.file),
                         "document_2025-01-13_14-30-44.bin")


class GroupVersionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_same_name_different_size_is_a_group(self):
        small = touch(os.path.join(self.dir, "a.pdf"), 100)
        big = touch(os.path.join(self.dir, "a (1).pdf"), 200)
        self.assertEqual(downloader.group_versions(self.dir), [[(small, 100), (big, 200)]])

    def test_same_size_is_not_a_version_group(self):
        touch(os.path.join(self.dir, "a.pdf"), 100)
        touch(os.path.join(self.dir, "a (1).pdf"), 100)
        self.assertEqual(downloader.group_versions(self.dir), [])

    def test_a_lone_file_is_not_a_group(self):
        touch(os.path.join(self.dir, "a.pdf"), 100)
        self.assertEqual(downloader.group_versions(self.dir), [])

    def test_part_files_are_ignored(self):
        touch(os.path.join(self.dir, "a.pdf"), 100)
        touch(os.path.join(self.dir, "12.part"), 200)
        self.assertEqual(downloader.group_versions(self.dir), [])


class PlanVersionRepairTest(unittest.TestCase):
    """Un gruppo e' una lista di (percorso, dimensione) ordinata per dimensione."""

    def test_one_truncated_and_one_good_copy(self):
        group = [("/d/a.pdf", 58458112), ("/d/a (1).pdf", 121307231)]
        self.assertEqual(downloader.plan_version_repair(group),
                         ("/d/a.pdf", "/d/a (1).pdf", "/d/a.pdf"))

    def test_different_editions_are_left_alone(self):
        """Il caso Jujutsu: nessuna delle due dimensioni e' multiplo di chunk."""
        group = [("/d/a.pdf", 3168410), ("/d/a (1).pdf", 4949195)]
        self.assertIsNone(downloader.plan_version_repair(group))

    def test_nothing_to_rename_when_the_good_copy_has_the_clean_name(self):
        group = [("/d/a (1).pdf", downloader.CHUNK_SIZE), ("/d/a.pdf", 999999)]
        self.assertEqual(downloader.plan_version_repair(group),
                         ("/d/a (1).pdf", "/d/a.pdf", None))

    def test_a_group_of_only_truncated_files_is_not_repaired(self):
        group = [("/d/a.pdf", downloader.CHUNK_SIZE)]
        self.assertIsNone(downloader.plan_version_repair(group))

    def test_more_than_one_truncated_copy_is_not_repaired(self):
        group = [("/d/a.pdf", downloader.CHUNK_SIZE),
                 ("/d/a (1).pdf", downloader.CHUNK_SIZE * 2),
                 ("/d/a (2).pdf", downloader.CHUNK_SIZE * 3 + 1)]
        self.assertIsNone(downloader.plan_version_repair(group))


class ApplyVersionRepairsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_the_truncated_case_end_to_end(self):
        broken = touch(os.path.join(self.dir, "a.pdf"), downloader.CHUNK_SIZE)
        good = touch(os.path.join(self.dir, "a (1).pdf"), 999999)
        group = downloader.group_versions(self.dir)[0]
        removed, renamed, freed = downloader.apply_version_repairs(
            [downloader.plan_version_repair(group)])
        self.assertEqual((removed, renamed, freed), (1, 1, downloader.CHUNK_SIZE))
        self.assertEqual(os.listdir(self.dir), ["a.pdf"])
        self.assertEqual(os.path.getsize(broken), 999999)
        self.assertFalse(os.path.exists(good))

    def test_no_rename_needed(self):
        broken = touch(os.path.join(self.dir, "a (1).pdf"), downloader.CHUNK_SIZE)
        touch(os.path.join(self.dir, "a.pdf"), 999999)
        group = downloader.group_versions(self.dir)[0]
        removed, renamed, _ = downloader.apply_version_repairs(
            [downloader.plan_version_repair(group)])
        self.assertEqual((removed, renamed), (1, 0))
        self.assertEqual(os.listdir(self.dir), ["a.pdf"])
        self.assertFalse(os.path.exists(broken))


class ResolveChannelDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_a_folder_name_sanitize_would_mangle_is_still_found(self):
        """'Bulletin! - Public Archive' esiste col punto esclamativo, ma sanitize lo toglie:
        il nome esatto va provato prima."""
        wanted = "Bulletin! - Public Archive"
        os.makedirs(os.path.join(self.dir, wanted))
        self.assertEqual(downloader.resolve_channel_dir(self.dir, wanted),
                         os.path.join(self.dir, wanted))

    def test_the_sanitized_name_is_the_fallback(self):
        os.makedirs(os.path.join(self.dir, "Bulletin - Public Archive"))
        self.assertEqual(
            downloader.resolve_channel_dir(self.dir, "Bulletin! - Public Archive"),
            os.path.join(self.dir, "Bulletin - Public Archive"))


class WalkFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_part_files_never_reach_the_indexes(self):
        touch(os.path.join(self.dir, "a.pdf"), 100)
        touch(os.path.join(self.dir, "7.part"), 50)
        self.assertEqual([os.path.basename(p) for p, _ in downloader.walk_files(self.dir)],
                         ["a.pdf"])


class BuildEntryTest(unittest.TestCase):
    def test_records_the_metadata_needed_for_deduplication(self):
        entry = downloader.buildEntry(12, name="a.pdf", size=100, file_id=77)
        self.assertEqual(entry["id"], 12)
        self.assertEqual(entry["name"], "a.pdf")
        self.assertEqual(entry["size"], 100)
        self.assertEqual(entry["media_id"], 77)
        self.assertTrue(entry["completed"])

    def test_metadata_is_optional(self):
        entry = downloader.buildEntry(12)
        self.assertEqual(entry["id"], 12)
        self.assertIsNone(entry["name"])


if __name__ == "__main__":
    # buffer=True nasconde le stampe di download_channel quando i test passano
    unittest.main(buffer=True)
