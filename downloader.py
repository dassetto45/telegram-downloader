# -*- coding: utf-8 -*-
import argparse
import asyncio
from datetime import datetime
import os
import shutil
import sys
import time
import requests
import json
import re
from telethon.sync import TelegramClient

__version__ = "1.0.0"

TRACKING_FILENAME = "downloaded.json"
DEFAULT_PATH = "./download"
# quante voci accumulare prima di riscrivere il file di sincronizzazione
FLUSH_EVERY = 10
# ' (1)' che telethon aggiunge quando un nome file e' gia' occupato
DUP_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")
# si scrive qui e si rinomina solo a download completato
PART_SUFFIX = ".part"
# 128 KiB e' il part size minimo di telethon (get_appropriated_part_size restituisce
# 128, 256 o 512 KB): un download interrotto finisce sempre su un multiplo di questo
CHUNK_SIZE = 131072
DOWNLOAD_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 5  # moltiplicato per il numero del tentativo
# Windows e OneDrive non ammettono questi caratteri e li sostituiscono con '_', quindi
# 'Report 2024: Volume 1.pdf' su Telegram e' 'Report 2024_ Volume 1.pdf' su disco
FORBIDDEN_CHARS = str.maketrans({char: "_" for char in '\\/:*?"<>|'})


def ensure_event_loop():
    """telethon.sync runs its coroutines on the thread's current event loop.

    Python 3.12 deprecated and 3.14 removed the implicit loop creation done by
    asyncio.get_event_loop(), so on those versions we have to install one ourselves.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download media files from a Telegram chat/channel.")
    parser.add_argument("--version", action="version",
                        version=f"telegram-downloader {__version__}")
    parser.add_argument("--channel", metavar="NAME",
                        help="chat/channel name (skips the interactive list). "
                             "Without it the script asks for everything at the prompt.")
    parser.add_argument("--search", metavar="TEXT",
                        help="only messages matching this text (Telegram full-text search "
                             "on captions and file names)")
    parser.add_argument("--ext", metavar="LIST",
                        help="comma separated list of allowed extensions, e.g. pdf,zip")
    parser.add_argument("--dry-run", action="store_true",
                        help="list matching files and total size without downloading")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="stop after scanning N messages (handy to try out a query)")
    parser.add_argument("--allow-duplicates", action="store_true",
                        help="download a file even when one with the same name and size "
                             "is already there")
    parser.add_argument("--find-duplicates", action="store_true",
                        help="list the duplicate files already on disk and exit. Works "
                             "offline: it never connects to Telegram")
    parser.add_argument("--delete", action="store_true",
                        help="with --find-duplicates, remove the extra copies (asks first)")
    return parser.parse_args()


def get_config():
    if not os.path.exists("sessions"):
        os.makedirs("sessions")
    if os.path.exists("config.json"):
        try:
            with open("config.json") as fp:
                config = json.load(fp)
                return config
        except (OSError, ValueError) as err:
            print(f"Cannot read config.json: {err}")
            sys.exit(1)
    else:
        print("Let's make some config!")
        print(f"Insert full path for your downloads: (default path is {DEFAULT_PATH})")
        path = input()
        if path == "":
            path = DEFAULT_PATH
        print("Insert your Api ID: ")
        input_api_id = input()
        print("Insert your Api HASH: ")
        input_api_hash = input()
        print(
            "Notify me when downloads are done (you'll need your account id and a bot token) [y/N]")
        input_notify = input()
        if input_notify == "":
            input_notify = False
        else:
            input_notify = True
            print("Your user id: ")
            input_user_id = input()
            print("Your BOT token: ")
            input_bot_token = input()
        configObject = []
        if input_notify == False:
            configObject.append({
                "api_id": int(input_api_id),
                "api_hash": input_api_hash,
                "notify": input_notify,
                "path": path
            })
        else:
            configObject.append({
                "api_id": int(input_api_id),
                "api_hash": input_api_hash,
                "notify": input_notify,
                "user_id": int(input_user_id),
                "bot_token": input_bot_token,
                "path": path
            })
        jsonString = json.dumps(configObject, default=str)
        with open("config.json", 'w') as file:
            file.write(jsonString)
            print("Configuration completed!")
        with open("config.json") as fp:
            listObj = json.load(fp)
            return listObj

# stampa progresso download


def bytes_to_mb(byte_amount):
    return byte_amount / (1024 * 1024)


def callback(current, total):
    print(f"Downloaded {bytes_to_mb(current):.1f} / {bytes_to_mb(total):.1f} MB"
          f" ({current / total:.1%})", end="\r")

# legge file sincronizzazione


def readFile(channelName, filename):
    try:
        with open(os.path.join(channelName, filename)) as fp:
            listObj = json.load(fp)
            return listObj
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as err:
        print(f"Cannot read {filename}, starting from scratch: {err}")
        return []

# scrive file sincronizzazione


def writeFile(aList, channelName, filename):
    jsonString = json.dumps(aList, default=str)
    with open(os.path.join(channelName, filename), 'w') as file:
        file.write(jsonString)
        return True

# aggiunge un oggetto alla lista di sincronizzazione (in memoria)


def buildEntry(message_id, name=None, size=None, file_id=None):
    """`name`, `size` e `file_id` servono a riconoscere i doppioni nei run successivi.

    Le voci scritte dalle versioni precedenti hanno solo `id`: chi legge usa `.get()`.
    """
    return {
        "id": message_id,
        "date_time": datetime.now(),
        "completed": True,
        "name": name,
        "size": size,
        "media_id": file_id
    }


def sanitize(name):
    return re.sub(r'[^\w_. -]', '', name).strip() or "chat"


def normalize_extensions(raw):
    """'pdf, .ZIP' -> {'pdf', 'zip'}. Empty input means "no extension filter"."""
    if not raw:
        return set()
    return {part.strip().lstrip(".").lower()
            for part in raw.split(",") if part.strip()}


def message_extension(message):
    """Extension of the attached file, lowercase and without the dot, or None."""
    media_file = getattr(message, "file", None)
    if media_file is None:
        return None
    ext = media_file.ext
    if not ext:
        ext = os.path.splitext(media_file.name or "")[1]
    if not ext:
        return None
    return ext.lstrip(".").lower()


def matches_filters(message, extensions):
    """True if the message should be downloaded. Pure: no network, no filesystem."""
    if not extensions:
        return True
    ext = message_extension(message)
    if ext is None:
        return False
    return ext in extensions


def slug_for(search, extensions):
    """Sub folder name for this particular search."""
    if search:
        return sanitize(search).lower().replace(" ", "_")
    if extensions:
        return "ext_" + "_".join(sorted(extensions))
    return "all"


# --- deduplica ---------------------------------------------------------------
# Lo stesso file viene ricaricato in canale piu volte con message id diversi.
# Telegram non espone un hash del contenuto, e comunque non servirebbe: due copie
# dello stesso pdf possono differire di pochi byte di metadati. Si confronta quindi
# cio che si conosce prima di scaricare: nome, dimensione e id del media.


def normalize_name(name):
    """'Newsletter [Issue 290] (1).PDF' -> 'newsletter [issue 290].pdf'.

    None se dal nome non si ricava nulla di confrontabile.
    """
    if not name:
        return None
    stem, ext = os.path.splitext(name)
    stem = DUP_SUFFIX_RE.sub("", stem)
    stem = re.sub(r"\s+", " ", stem).strip().lower()
    if not stem:
        return None
    return (stem + ext.lower()).translate(FORBIDDEN_CHARS)


def dup_key(name, size):
    """Chiave di deduplica, o None se nome o dimensione non sono utilizzabili.

    I media senza nome (le foto) non hanno chiave: per loro conta solo `media_id`,
    perche due foto diverse possono pesare esattamente uguale.
    """
    normalized = normalize_name(name)
    if not normalized or not size:
        return None
    return f"{normalized}|{size}"


def media_id(message):
    """Id del documento o della foto: identico = stesso file sui server Telegram."""
    for attribute in ("document", "photo"):
        media = getattr(message, attribute, None)
        if media is not None:
            found = getattr(media, "id", None)
            if found is not None:
                return found
    return None


def walk_files(directory):
    """(percorso, dimensione) di ogni file sotto `directory`.

    Salta il tracking file e i temporanei: un `.part` residuo non e' un file scaricato
    e non deve finire negli indici ne' fra i doppioni.
    """
    for root, _dirs, files in os.walk(directory):
        for filename in files:
            if filename == TRACKING_FILENAME or filename.endswith(PART_SUFFIX):
                continue
            path = os.path.join(root, filename)
            try:
                yield path, os.path.getsize(path)
            except OSError:
                continue


def build_duplicate_index(channel_dir, tracking):
    """Cosa e' gia' stato preso, e da dove.

    Due sorgenti perche' lo storico scritto dalle versioni precedenti contiene solo
    i message id: senza lo scan del disco i file gia' scaricati sarebbero invisibili.
    Il valore di ogni chiave e' leggibile ("msg 45" o il percorso relativo) e finisce
    nel messaggio che spiega perche' un file e' stato saltato.
    """
    by_key = {}
    by_media = {}
    by_name = {}
    for entry in tracking:
        origin = f"msg {entry.get('id')}"
        key = dup_key(entry.get("name"), entry.get("size"))
        if key:
            by_key.setdefault(key, origin)
        if entry.get("media_id"):
            by_media.setdefault(entry["media_id"], origin)
    for path, size in walk_files(channel_dir):
        key = dup_key(os.path.basename(path), size)
        if key:
            by_key.setdefault(key, os.path.relpath(path, channel_dir))
        # by_name risponde a "esiste un file con questo nome, e quanto pesa?", cosa che
        # by_key non puo' fare perche' la dimensione fa parte della chiave. Serve per
        # riconoscere i tronchi, che hanno per definizione la dimensione sbagliata.
        normalized = normalize_name(os.path.basename(path))
        if normalized:
            known = by_name.get(normalized)
            if known is None or size > known[1]:
                by_name[normalized] = (path, size)
    return {"by_key": by_key, "by_media": by_media, "by_name": by_name}


def find_truncated(index, name, expected_size):
    """(percorso, dimensione) del file tronco con questo nome, oppure None."""
    normalized = normalize_name(name)
    if not normalized:
        return None
    known = index["by_name"].get(normalized)
    if known and looks_truncated(known[1], expected_size):
        return known
    return None


def find_duplicate(index, name, size, message_media_id):
    """Dove sta la copia gia' presente, oppure None."""
    if message_media_id and message_media_id in index["by_media"]:
        return index["by_media"][message_media_id]
    key = dup_key(name, size)
    if key:
        return index["by_key"].get(key)
    return None


def remember_download(index, message, name, size, message_media_id, path=None):
    """Registra il file appena preso, cosi' due doppioni nella stessa scansione si vedono."""
    origin = f"msg {message.id}"
    key = dup_key(name, size)
    if key:
        index["by_key"].setdefault(key, origin)
    if message_media_id:
        index["by_media"].setdefault(message_media_id, origin)
    normalized = normalize_name(name)
    if normalized:
        # sovrascrive: se prima c'era un tronco, ora il file buono e' questo
        index["by_name"][normalized] = (path, size)


def keep_rank(path):
    """Ordinamento delle copie: prima quella da tenere.

    Senza suffisso ` (N)` batte con suffisso, a pari merito vince l'mtime piu' vecchio.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    suffixed = bool(DUP_SUFFIX_RE.search(stem))
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    return (suffixed, mtime, path)


def group_duplicates(channel_dir):
    """Gruppi di 2+ file con la stessa chiave, ognuno con la copia da tenere per prima."""
    groups = {}
    for path, size in walk_files(channel_dir):
        key = dup_key(os.path.basename(path), size)
        if key:
            groups.setdefault(key, []).append(path)
    duplicates = [sorted(paths, key=keep_rank)
                  for paths in groups.values() if len(paths) > 1]
    duplicates.sort(key=lambda paths: paths[0])
    return duplicates


# --- file interrotti ---------------------------------------------------------
# download_media scrive direttamente sul nome finale, quindi un'interruzione lascia
# un file parziale che sembra valido. Si scrive su un temporaneo e si rinomina solo
# dopo aver verificato la dimensione, cosi' non puo' piu' succedere.
# Per riconoscere i tronchi che ci sono gia' bastano i metadati: leggerne il contenuto
# forzerebbe OneDrive a scaricare l'intera libreria dal cloud.


def looks_truncated(size, expected_size):
    """Un download interrotto finisce sempre su un confine di chunk.

    Servono entrambe le condizioni. Solo "piu' piccolo del previsto" non basta: un
    ricaricamento puo' essere un'edizione diversa e valida, e sovrascriverla sarebbe
    una perdita di dati.
    """
    return bool(size and expected_size and size < expected_size
                and size % CHUNK_SIZE == 0)


def remove_quietly(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def clear_part_files(directory):
    """Cancella i temporanei rimasti da un run interrotto. Ritorna quanti erano."""
    removed = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for filename in names:
        if filename.endswith(PART_SUFFIX):
            removed += remove_quietly(os.path.join(directory, filename))
    return removed


def target_name(message, media_file):
    """Il nome che telethon userebbe: file_name se c'e', altrimenti kind_data-ora.ext."""
    name = getattr(media_file, "name", None)
    if name:
        return name
    kind = "photo" if getattr(message, "photo", None) is not None else "document"
    ext = getattr(media_file, "ext", None) or ".bin"
    return f"{kind}_{message.date:%Y-%m-%d_%H-%M-%S}{ext}"


def next_free_name(path):
    """'a.pdf' occupato -> 'a (1).pdf', come fa telethon."""
    directory, filename = os.path.split(path)
    stem, ext = os.path.splitext(filename)
    index = 1
    while True:
        candidate = os.path.join(directory, f"{stem} ({index}){ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def resolve_target_path(target_dir, name, expected_size, truncated_path=None):
    """Su quale percorso rinominare il temporaneo.

    `truncated_path` e' il file rotto trovato nell'indice, che puo' stare in una
    sottocartella diversa da `target_dir`: in quel caso si sostituisce dov'e', senza
    lasciare in giro il rotto e una copia nuova.
    """
    if truncated_path:
        return truncated_path
    path = os.path.join(target_dir, name)
    if not os.path.exists(path):
        return path
    if looks_truncated(os.path.getsize(path), expected_size):
        return path
    return next_free_name(path)  # file diverso e completo: ' (1)' come oggi


def download_with_retries(client, message, part_path, expected_size):
    """True se il file e' arrivato intero. Il temporaneo viene sempre ripulito."""
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            client.download_media(message, part_path, progress_callback=callback)
            print()
            written = os.path.getsize(part_path)
            if expected_size and written < expected_size:
                raise IOError(f"got {written} of {expected_size} bytes")
            return True
        except KeyboardInterrupt:
            # non discende da Exception: qui si passa solo per ripulire
            remove_quietly(part_path)
            raise
        except Exception as err:
            remove_quietly(part_path)
            print(f"    attempt {attempt}/{DOWNLOAD_ATTEMPTS} failed: {err}")
            if attempt == DOWNLOAD_ATTEMPTS:
                return False
            time.sleep(RETRY_WAIT_SECONDS * attempt)
    return False


def group_versions(channel_dir):
    """Gruppi di 2+ file con lo stesso nome normalizzato ma dimensioni diverse.

    Ogni gruppo e' una lista di (percorso, dimensione) ordinata per dimensione.
    """
    groups = {}
    for path, size in walk_files(channel_dir):
        normalized = normalize_name(os.path.basename(path))
        if normalized:
            groups.setdefault(normalized, []).append((path, size))
    versions = [sorted(items, key=lambda item: item[1]) for items in groups.values()
                if len(items) > 1 and len({size for _, size in items}) > 1]
    versions.sort(key=lambda items: items[0][0])
    return versions


def truncated_in_group(group):
    """I file del gruppo che sembrano download interrotti.

    Offline la dimensione attesa non si conosce, quindi si prende come riferimento la
    copia piu' grande: e' esattamente il caso in cui il rotto e' stato affiancato da
    quella buona, che e' come questi file finiscono sul disco.
    """
    biggest = group[-1][1]
    return [(path, size) for path, size in group if looks_truncated(size, biggest)]


def clean_name(path):
    """Lo stesso percorso senza il suffisso ' (N)'."""
    directory, filename = os.path.split(path)
    stem, ext = os.path.splitext(filename)
    return os.path.join(directory, DUP_SUFFIX_RE.sub("", stem) + ext)


def plan_version_repair(group):
    """(tronco da cancellare, copia buona, nome su cui rinominarla o None).

    None se il gruppo non e' riparabile: nessun tronco (sono edizioni diverse), o piu'
    di uno, o solo tronchi. In quei casi non si tocca niente.
    """
    broken = truncated_in_group(group)
    if len(broken) != 1 or len(broken) == len(group):
        return None
    broken_path = broken[0][0]
    good_path = group[-1][0]
    if good_path == broken_path:
        return None
    target = clean_name(good_path)
    return (broken_path, good_path, None if target == good_path else target)


def list_dialogs(client):
    dialogs = [(d.name, d.entity) for d in client.iter_dialogs()]
    dialogs.sort(key=lambda item: (item[0] or "").lower())
    return dialogs


def resolve_channel(client, wanted):
    """Return (name, entity). Accepts a name or a 1-based index from the printed list."""
    dialogs = list_dialogs(client)
    if wanted is None:
        for index, (name, _) in enumerate(dialogs, start=1):
            print(f"{index:4d}. {name}")
        print('Select what chat/channel you want to download media files (name or number): ')
        wanted = input().strip()

    if wanted.isdigit():
        index = int(wanted)
        if 1 <= index <= len(dialogs):
            return dialogs[index - 1]
        print(f"There is no chat number {index}.")
        sys.exit(1)

    matches = [d for d in dialogs if d[0] == wanted]
    if not matches:
        matches = [d for d in dialogs if (d[0] or "").lower() == wanted.lower()]
    if not matches:
        print(f"No chat/channel named {wanted!r}. Run without --channel to see the list.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"{len(matches)} chats are named {wanted!r}. Pick one by number:")
        for index, (name, entity) in enumerate(matches, start=1):
            print(f"{index:4d}. {name} (id {entity.id})")
        choice = input().strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(matches):
            print("Invalid choice.")
            sys.exit(1)
        return matches[int(choice) - 1]
    return matches[0]


def list_channel_dirs(base_path):
    try:
        folders = [entry.name for entry in os.scandir(base_path) if entry.is_dir()]
    except OSError as err:
        print(f"Cannot read {base_path}: {err}")
        sys.exit(1)
    folders.sort(key=lambda folder: folder.lower())
    return folders


def resolve_channel_dir(base_path, wanted):
    """Folder of a channel already on disk, without touching Telegram.

    Accepts the folder name or a 1-based index from the printed list.
    """
    if wanted:
        # il nome esatto va provato per primo: sanitize toglierebbe caratteri che nella
        # cartella ci sono, come il '!' di 'Bulletin! - Public Archive'
        for candidate in (wanted, sanitize(wanted)):
            path = os.path.join(base_path, candidate)
            if os.path.isdir(path):
                return path
        print(f"There is no folder for {wanted!r} under {base_path}.")

    folders = list_channel_dirs(base_path)
    if not folders:
        print(f"Nothing downloaded under {base_path} yet.")
        sys.exit(1)
    for index, folder in enumerate(folders, start=1):
        print(f"{index:4d}. {folder}")
    print("Which channel do you want to check (name or number): ")
    choice = input().strip()
    if choice.isdigit():
        if 1 <= int(choice) <= len(folders):
            return os.path.join(base_path, folders[int(choice) - 1])
        print(f"There is no folder number {choice}.")
        sys.exit(1)
    matches = [folder for folder in folders if folder.lower() == choice.lower()]
    if not matches:
        print("Invalid choice.")
        sys.exit(1)
    return os.path.join(base_path, matches[0])


def print_duplicate_groups(channel_dir, groups):
    """Stampa i gruppi di copie identiche. Ritorna (file in eccesso, byte)."""
    extra_files = 0
    extra_bytes = 0
    for paths in groups:
        # tutte le copie di un gruppo hanno la stessa dimensione, e' parte della chiave
        size = os.path.getsize(paths[0])
        print(f"{os.path.basename(paths[0])} ({bytes_to_mb(size):.1f} MB) x{len(paths)}")
        for position, path in enumerate(paths):
            when = datetime.fromtimestamp(os.path.getmtime(path))
            print(f"    {'kept ' if position == 0 else 'extra'}  {when:%d/%m/%Y}  "
                  f"{os.path.relpath(path, channel_dir)}")
        extra_files += len(paths) - 1
        extra_bytes += size * (len(paths) - 1)
    return extra_files, extra_bytes


def print_version_groups(channel_dir, versions):
    """Stampa i gruppi stesso-nome-dimensione-diversa. Ritorna (riparazioni, byte)."""
    repairs = []
    broken_bytes = 0
    for group in versions:
        repair = plan_version_repair(group)
        broken = {path for path, _ in truncated_in_group(group)}
        print(f"{clean_name(os.path.basename(group[-1][0]))}")
        for path, size in group:
            label = "truncated" if path in broken else "complete "
            print(f"    {label}  {bytes_to_mb(size):9.1f} MB  "
                  f"{os.path.relpath(path, channel_dir)}")
        if repair:
            broken_path, _good_path, rename_to = repair
            broken_bytes += dict(group)[broken_path]
            repairs.append(repair)
            print("       -> would delete the truncated copy" +
                  (f" and rename the good one to {os.path.basename(rename_to)}"
                   if rename_to else ""))
        else:
            print("       -> different versions, left alone")
    return repairs, broken_bytes


def apply_version_repairs(repairs):
    """Cancella ogni tronco e rinomina la copia buona sul nome pulito, se e' libero.

    Ritorna (tronchi cancellati, copie rinominate, byte liberati).
    """
    removed = 0
    renamed = 0
    freed = 0
    for broken_path, good_path, rename_to in repairs:
        try:
            size = os.path.getsize(broken_path)
            os.remove(broken_path)
        except OSError as err:
            print(f"Cannot delete {broken_path}: {err}")
            continue
        removed += 1
        freed += size
        # il nome pulito si libera solo dopo la cancellazione, quindi si rinomina qui
        if rename_to and not os.path.exists(rename_to):
            try:
                os.rename(good_path, rename_to)
                renamed += 1
            except OSError as err:
                print(f"Cannot rename {good_path}: {err}")
    return removed, renamed, freed


def report_duplicates(base_path, wanted, delete):
    """Elenca doppioni e file tronchi gia' su disco e, se richiesto, li sistema."""
    channel_dir = resolve_channel_dir(base_path, wanted)
    print(f"Looking for duplicates in {channel_dir}\n")
    groups = group_duplicates(channel_dir)
    versions = group_versions(channel_dir)

    extra_files = extra_bytes = 0
    if groups:
        extra_files, extra_bytes = print_duplicate_groups(channel_dir, groups)
    repairs = []
    broken_bytes = 0
    if versions:
        print(f"\nSame name, different size ({len(versions)} group(s)):\n")
        repairs, broken_bytes = print_version_groups(channel_dir, versions)

    if not extra_files and not repairs:
        print("\nNothing to clean up." if versions else "No duplicate found.")
        return

    print()
    if extra_files:
        print(f"{extra_files} extra file(s) in {len(groups)} group(s), "
              f"{bytes_to_mb(extra_bytes):.1f} MB recoverable.")
    if repairs:
        print(f"{len(repairs)} truncated file(s), "
              f"{bytes_to_mb(broken_bytes):.1f} MB recoverable.")
    if not delete:
        print("Nothing was changed. Add --delete to clean up.")
        return

    what = []
    if extra_files:
        what.append(f"{extra_files} duplicate(s)")
    if repairs:
        what.append(f"{len(repairs)} truncated file(s)")
    print(f"\nDelete {' and '.join(what)}, free "
          f"{bytes_to_mb(extra_bytes + broken_bytes):.1f} MB? [y/N]")
    if input().strip().lower() not in ("y", "yes"):
        print("Aborted, nothing was changed.")
        return

    removed = 0
    freed = 0
    for paths in groups:
        for path in paths[1:]:
            try:
                size = os.path.getsize(path)
                os.remove(path)
            except OSError as err:
                print(f"Cannot delete {path}: {err}")
                continue
            removed += 1
            freed += size
    print(f"Deleted {removed} duplicate(s), {bytes_to_mb(freed):.1f} MB freed.")

    if repairs:
        repaired, renamed, recovered = apply_version_repairs(repairs)
        print(f"Deleted {repaired} truncated file(s), {bytes_to_mb(recovered):.1f} MB "
              f"freed, {renamed} good copy(ies) renamed.")


def migrate_legacy_tracking(base_path, folder, channel_dir):
    """Older versions joined path and channel name without a separator.

    Recover that tracking file so a fixed path doesn't re-download everything.
    """
    legacy_dir = base_path + folder
    if os.path.abspath(legacy_dir) == os.path.abspath(channel_dir):
        return
    legacy_file = os.path.join(legacy_dir, TRACKING_FILENAME)
    new_file = os.path.join(channel_dir, TRACKING_FILENAME)
    if os.path.exists(legacy_file) and not os.path.exists(new_file):
        shutil.copy2(legacy_file, new_file)
        print(f"Imported previous download history from {legacy_dir}")


def download_channel(client, entity, channel_dir, target_dir, search, extensions,
                     dry_run, limit, allow_duplicates=False):
    tracking = readFile(channel_dir, TRACKING_FILENAME)
    known_ids = {entry["id"] for entry in tracking if "id" in entry}
    index = build_duplicate_index(channel_dir, tracking)

    matched = 0
    downloaded = 0
    skipped = 0
    duplicates = 0
    incomplete = 0
    failed = 0
    total_bytes = 0
    duplicate_bytes = 0
    pending = 0

    if not dry_run:
        leftovers = clear_part_files(target_dir)
        if leftovers:
            print(f"Cleared {leftovers} leftover .part file(s) from an interrupted run.")

    try:
        for message in client.iter_messages(entity, search=search, limit=limit):
            if message.media is None:
                continue
            if not matches_filters(message, extensions):
                continue

            matched += 1
            media_file = getattr(message, "file", None)
            size = getattr(media_file, "size", None) or 0
            total_bytes += size
            # raw_name puo' essere None (le foto non hanno nome): la deduplica usa
            # quello, il nome col fallback serve solo a stampare qualcosa di sensato
            raw_name = getattr(media_file, "name", None)
            name = raw_name or f"message_{message.id}"

            message_media_id = media_id(message)
            # Il tronco si valuta per primo: una voce in downloaded.json o una chiave
            # di deduplica lo mascherebbero e il file resterebbe rotto per sempre.
            # by_name tiene la copia piu' grande, quindi se accanto al rotto c'e' anche
            # quella buona qui non scatta nulla e si cade sul ramo duplicato.
            truncated = find_truncated(index, raw_name, size)

            if truncated is None:
                if message.id in known_ids:
                    skipped += 1
                    continue
                if not allow_duplicates:
                    origin = find_duplicate(index, raw_name, size, message_media_id)
                    if origin:
                        duplicates += 1
                        duplicate_bytes += size
                        print(f"DUP [{message.id}] {name} - already have it ({origin})")
                        continue

            if truncated:
                incomplete += 1
                print(f"INCOMPLETE [{message.id}] {name} "
                      f"{bytes_to_mb(truncated[1]):.1f}/{bytes_to_mb(size):.1f} MB"
                      f" - downloading again")
            else:
                print(f"[{message.id}] {name}"
                      + (f" ({bytes_to_mb(size):.1f} MB)" if dry_run else ""))

            # anche in dry run il file va registrato, altrimenti due doppioni nella
            # stessa scansione risultano entrambi da scaricare
            if dry_run:
                remember_download(index, message, raw_name, size, message_media_id)
                continue

            part_path = os.path.join(target_dir, f"{message.id}{PART_SUFFIX}")
            if not download_with_retries(client, message, part_path, size):
                failed += 1
                print(f"    giving up on [{message.id}] {name}")
                continue
            final_path = resolve_target_path(
                target_dir, target_name(message, media_file), size,
                truncated[0] if truncated else None)
            os.replace(part_path, final_path)

            tracking.append(buildEntry(message.id, raw_name, size, message_media_id))
            known_ids.add(message.id)
            remember_download(index, message, raw_name, size, message_media_id,
                              final_path)
            downloaded += 1
            pending += 1
            if pending >= FLUSH_EVERY:
                writeFile(tracking, channel_dir, TRACKING_FILENAME)
                pending = 0
    finally:
        # anche su Ctrl-C: i file gia' scaricati non vanno perduti dal tracking
        if pending:
            writeFile(tracking, channel_dir, TRACKING_FILENAME)

    return {"matched": matched, "downloaded": downloaded,
            "skipped": skipped, "duplicates": duplicates,
            "incomplete": incomplete, "failed": failed,
            "total_bytes": total_bytes, "duplicate_bytes": duplicate_bytes}


def describe_filters(search, extensions):
    parts = []
    if search:
        parts.append(f"search={search!r}")
    if extensions:
        parts.append("ext=" + ",".join(sorted(extensions)))
    return ", ".join(parts) if parts else "no filter"


def sendNotification(config, channelName, summary, search, extensions):
    token = config[0]['bot_token']
    userId = config[0]['user_id']
    url = f"https://api.telegram.org/bot{token}"
    text = (f"Download from {channelName} is over "
            f"({describe_filters(search, extensions)}): "
            f"{summary['downloaded']} new file(s)")
    if summary.get('duplicates'):
        text += f", {summary['duplicates']} duplicate(s) skipped"
    if summary.get('incomplete'):
        text += f", {summary['incomplete']} incomplete file(s) redone"
    if summary.get('failed'):
        text += f", {summary['failed']} FAILED"
    params = {"chat_id": userId, "text": text}
    requests.get(url + "/sendMessage", params=params)


def main():
    args = parse_args()
    config = get_config()
    base_path = config[0].get('path') or DEFAULT_PATH

    # censimento dei doppioni: lavora sul disco, non serve collegarsi a Telegram
    if args.find_duplicates:
        report_duplicates(base_path, args.channel, args.delete)
        return
    if args.delete:
        print("--delete only works together with --find-duplicates.")
        sys.exit(1)

    api_id = config[0]['api_id']
    api_hash = config[0]['api_hash']

    interactive = args.channel is None
    search = args.search
    extensions = normalize_extensions(args.ext)

    ensure_event_loop()
    client = TelegramClient('sessions/test_session_101', api_id, api_hash)
    client.start()

    channel_name, entity = resolve_channel(client, args.channel)

    if interactive:
        if search is None:
            print('Search text to filter messages (empty = no filter): ')
            search = input().strip() or None
        if not extensions:
            print('File extensions to keep, comma separated e.g. pdf,zip (empty = all): ')
            extensions = normalize_extensions(input())

    folder = sanitize(channel_name)
    channel_dir = os.path.join(base_path, folder)
    target_dir = os.path.join(channel_dir, slug_for(search, extensions))
    if not args.dry_run:
        os.makedirs(target_dir, exist_ok=True)
        migrate_legacy_tracking(base_path, folder, channel_dir)

    print(f"Channel: {channel_name} | {describe_filters(search, extensions)}")
    print(f"Destination: {target_dir}")
    if args.dry_run:
        print("Dry run: nothing will be downloaded.\n")

    summary = download_channel(client, entity, channel_dir, target_dir,
                               search, extensions, args.dry_run, args.limit,
                               args.allow_duplicates)

    print(f"\n{summary['matched']} matching file(s), "
          f"{bytes_to_mb(summary['total_bytes']):.1f} MB total, "
          f"{summary['skipped']} already downloaded.")
    if summary['duplicates']:
        print(f"{summary['duplicates']} duplicate(s) skipped, "
              f"{bytes_to_mb(summary['duplicate_bytes']):.1f} MB saved. "
              f"Use --allow-duplicates to download them anyway.")
    if summary['incomplete']:
        print(f"{summary['incomplete']} incomplete file(s) "
              + ("to download again." if args.dry_run else "downloaded again."))
    if summary['failed']:
        print(f"{summary['failed']} file(s) failed after {DOWNLOAD_ATTEMPTS} attempts. "
              f"Run the same command again to retry them.")
    if args.dry_run:
        print("Dry run finished, no file was written.")
        return

    print(f"Downloaded {summary['downloaded']} new file(s) into {target_dir}")
    if config[0].get('notify') == True:
        sendNotification(config, channel_name, summary, search, extensions)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Nothing half written was left behind.")
        sys.exit(130)
