import time

from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
from librespot.metadata import TrackId
from librespot.core import Session

from pydub import AudioSegment

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC

from colorama import Fore, Back, Style

from multiprocessing import Pool, freeze_support, shared_memory
from urllib.request import urlopen
from dataclasses import dataclass
from math import ceil, floor
from enum import Enum
import traceback
import datetime
import json
import os

TMP_DIR = os.path.join(os.getcwd(), "tmp")


@dataclass
class Playlist:
    name: str
    size: int
    directory: os.path
    tracks: list[dict]


@dataclass
class Track:
    id: TrackId
    name: str
    artists: list[str]
    album: str
    added: datetime.datetime
    image_url: list[str]
    track_number: int
    disc_number: int


@dataclass
class DownloadArgs:
    """This seems useless, but it makes passing args into the pool easier"""
    data: list[tuple[Track, str, str, int, int]]


class DownloadState(Enum):
    SUCCESS = 1
    FAIL = 2
    SKIP = 3


class LogAction(Enum):
    DOWNLOAD = "Downloading"
    CONVERT = "Converting"
    SKIP = "Skipping"
    ERROR = "ERROR"
    WARN = "Warning! (Rate limit?)"


def compatible(text):
    return (text.replace("<", "-").replace(">", "-").replace(":", ";")
            .replace('"', "'").replace("/", "-").replace("\\", "-")
            .replace("|", ";").replace("?", "!").replace("*", "#"))[:120]


def console_log_file(action: LogAction, playlist_name: str, playlist_index: int, playlist_size: int, filename: str):
    style = Style.RESET_ALL
    match action:
        case LogAction.ERROR: style += Back.RED + Fore.BLACK + Style.BRIGHT
        case LogAction.WARN: style += Back.YELLOW + Fore.BLACK + Style.BRIGHT
        case LogAction.SKIP: style += Fore.LIGHTBLACK_EX
        case LogAction.DOWNLOAD: style += Fore.CYAN
        case LogAction.CONVERT: style += Fore.GREEN
    try:
        print(f"{style}{playlist_name}: #{playlist_index + 1}/{playlist_size} {action.value} ({filename})")
    except UnicodeEncodeError:
        print(f"{style}{playlist_name}: #{playlist_index + 1}/{playlist_size} {action.value}")


def console_log_progress(dl_state: DownloadState, process_id: int, sl: shared_memory.ShareableList):
    # Every ~10 seconds show full progress
    now = datetime.datetime.now().timestamp()
    last_shown = sl[-1]
    if now - last_shown > 10:  # Every ~10 seconds show full progress
        total, success, fail, skip = 0, 0, 0, 0
        for process in range(PROCESSES):
            total += sl[process*4]
            success += sl[process*4 + DownloadState.SUCCESS.value]
            fail += sl[process*4 + DownloadState.FAIL.value]
            skip += sl[process*4 + DownloadState.SKIP.value]

        done = success + fail + skip
        percentage = floor((done / total) * 100)

        style = Style.RESET_ALL + Back.GREEN + Fore.BLACK
        print(f"{style}Total progress: {done}/{total} ({percentage}%) "
              f"[{success} downloaded, {fail} failed, {skip} skipped] ")
        sl[-1] = now
        return

    if dl_state == DownloadState.SKIP:
        return

    total = sl[process_id]
    success = sl[process_id + DownloadState.SUCCESS.value]
    fail = sl[process_id + DownloadState.FAIL.value]
    skip = sl[process_id + DownloadState.SKIP.value]

    done = success + fail + skip
    percentage = floor((done/total) * 100)

    style = Style.RESET_ALL + Back.BLUE + Fore.BLACK
    print(f"{style}Process #{round(process_id/4)+1}: {success} downloaded, {fail} failed, {skip} skipped")
    print(Style.RESET_ALL, end="")


def console_log_info(text: str):
    style = Style.RESET_ALL + Fore.LIGHTMAGENTA_EX
    print(f"{style}{text}")


def download(output_path: str, processes: int):
    with open("spotify-playlists.json", "r") as f:
        playlists = json.loads(f.read())

    if not os.path.exists(output_path):
        os.mkdir(output_path)

    download_args = []
    total_songs = 0

    # Collect all tracks
    for _playlist in playlists:
        playlist = Playlist(
            name=_playlist["name"],
            size=len(_playlist["tracks"]),
            directory=os.path.join(output_path, compatible(_playlist["name"])),
            tracks=_playlist["tracks"]
        )

        if not os.path.exists(playlist.directory):
            os.mkdir(playlist.directory)

        console_log_info(f"Playlist {playlist.name} ({playlist.size} tracks)")

        for i, _track in enumerate(playlist.tracks):
            try:
                added_time = datetime.datetime.strptime(_track["added"], "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                added_time = datetime.datetime.strptime(_track["added"], "%YT%H:%M:%SZ")

            track = Track(
                id=TrackId.from_base62(_track["id"]),
                name=_track["name"].replace("\u2019", "'"),
                artists=_track["artists"],
                album=compatible(_track["album"]),
                added=added_time,
                image_url=_track["image_url"],
                track_number=_track["track_number"],
                disc_number=_track["disc_number"]
            )

            filename = f"{compatible(track.name)} - {compatible(', '.join(track.artists if len(track.artists) < 3 else track.artists[:2]))}"
            path = os.path.join(playlist.directory, filename)
            mp3_path = f"{path}.mp3"

            total_songs += 1

            if os.path.exists(mp3_path):
                if os.path.getsize(mp3_path) > 0:
                    console_log_file(LogAction.SKIP, playlist.name, i, playlist.size, filename)
                    continue

            download_args.append((track, playlist.directory, playlist.name, playlist.size, i))

    console_log_info(f"Downloading {len(download_args)}/{total_songs} total tracks.")

    # Shared list for progress
    # Contains 4 elements per process. That's why PROCESSES*4 here and also chunk*4 below.
    # The last element has a timestamp of the last time full progress was shown.
    shared_list = shared_memory.ShareableList([0 for _ in range(PROCESSES*4)] + [datetime.datetime.now().timestamp()])

    # Split download args equally among processes aka make args for each process
    process_args = []
    chunk_size = floor(len(download_args) / PROCESSES)
    for chunk in range(PROCESSES):
        start_pos = floor(chunk_size * chunk)
        end_pos = start_pos + chunk_size
        if chunk == PROCESSES - 1:  # On last chunk just take all the rest
            end_pos = len(download_args)
        process_args.append((DownloadArgs(data=download_args[start_pos:end_pos]), chunk*4, shared_list))

    for file in os.listdir(TMP_DIR):
        os.remove(os.path.join(TMP_DIR, file))

    with Pool(processes) as pool:
        pool.starmap(run_download_process, process_args)
        console_log_info("Done!")


def run_download_process(download_args: DownloadArgs, process_id: int, sl: shared_memory.ShareableList):
    session = Session.Builder().stored_file().create()
    sl[process_id] = len(download_args.data)

    console_log_info(f"Process {round(process_id/4)+1} created.")

    for i, args in enumerate(download_args.data):
        download_state = download_song(session, *args)
        sl[process_id + download_state.value] += 1

        console_log_progress(download_state, process_id, sl)


def download_song(session: Session, track: Track, directory: os.path, playlist_name: str, playlist_size: int,
                  playlist_index: int) -> DownloadState:
    filename = f"{compatible(track.name)} - {compatible(', '.join(track.artists if len(track.artists) < 3 else track.artists[:2]))}"
    path = os.path.join(directory, filename)
    error_path = f"{path}.error"
    tmp_path = os.path.join(TMP_DIR, f"{filename}.ogg.tmp")
    mp3_path = f"{path}.mp3"

    log_args = (playlist_name, playlist_index, playlist_size, filename)

    if os.path.exists(error_path):
        os.remove(error_path)

    # Check here again just to be safe
    if os.path.exists(mp3_path):
        if os.path.getsize(mp3_path) > 0:
            console_log_file(LogAction.SKIP, *log_args)
            return DownloadState.SKIP

    stream_result, stream_data = get_stream(session, track, log_args)
    if not stream_result:
        console_log_file(LogAction.ERROR, *log_args)
        with open(error_path, "w") as f:
            f.write(stream_data)
        os.utime(error_path, (track.added.timestamp(), track.added.timestamp()))
        return DownloadState.FAIL

    console_log_file(LogAction.DOWNLOAD, *log_args)

    stream_size = stream_data.input_stream.stream().size()
    with open(tmp_path, "wb") as f:
        f.write(stream_data.input_stream.stream().read(stream_size))

    convert_song(track, directory, playlist_name, playlist_size, playlist_index)
    return DownloadState.SUCCESS


def get_stream(session: Session, track: Track, log_args: tuple) -> tuple[bool, str] | tuple[bool, ...]:
    stream = None
    sleep_time = 5
    while stream is None:
        try:
            stream = session.content_feeder().load(
                track.id, VorbisOnlyAudioQuality(AudioQuality.VERY_HIGH), False, None
            )
        except (ValueError, OSError):
            pass
        except RuntimeError as e:
            if "Failed fetching audio key!" in f"{e}":
                console_log_file(LogAction.WARN, *log_args)
                time.sleep(sleep_time)
                sleep_time += 5
                continue

            return False, traceback.format_exc()

    return True, stream


def convert_song(track: Track, directory: os.path, playlist_name, playlist_size, playlist_index, *args):
    filename = f"{compatible(track.name)} - {compatible(', '.join(track.artists if len(track.artists) < 3 else track.artists[:2]))}"
    path = os.path.join(directory, filename)
    tmp_path = os.path.join(TMP_DIR, f"{filename}.ogg.tmp")
    tmp_mp3_path = f"{path}.mp3.part"
    mp3_path = f"{path}.mp3"

    console_log_file(LogAction.CONVERT, playlist_name, playlist_index, playlist_size, filename)

    AudioSegment.from_file(tmp_path, format="ogg").export(tmp_mp3_path, format="mp3", bitrate="320k")
    os.remove(tmp_path)

    audio = EasyID3(tmp_mp3_path)
    audio['artist'] = ', '.join(track.artists)
    audio['title'] = track.name
    audio['album'] = track.album
    audio['musicbrainz_trackid'] = ""
    audio['discnumber'] = f"{track.disc_number}"
    audio['tracknumber'] = f"{track.track_number}"
    audio.save()

    image = track.image_url
    image_data = urlopen(image).read()
    audio = ID3(tmp_mp3_path)
    audio['APIC'] = APIC(
        encoding=3,
        mime='image/png',
        type=3, desc=u'Cover',
        data=image_data
    )
    audio.save()

    # THIS IS ONLY FOR ALBUMS - PLEASE COMMENT OUT.
    folder_path = os.path.join(directory, "folder.png")
    if not os.path.exists(folder_path):
        with open(folder_path, "wb") as img_file:
            img_file.write(image_data)

    os.rename(tmp_mp3_path, mp3_path)
    os.utime(mp3_path, (track.added.timestamp(), track.added.timestamp()))


if __name__ == "__main__":
    if not os.path.exists("credentials.json"):
        while True:
            user_name = input("UserName: ")
            password = input("Password: ")
            try:
                Session.Builder().user_pass(user_name, password).create()
                break
            except RuntimeError:
                pass

    # Be careful about rate limiting. Don't use too many processes.
    PROCESSES = 1
    console_log_info(f"Running {PROCESSES} sessions.")

    console_log_info(f"Clearing old temp files...")
    if not os.path.exists(TMP_DIR):
        os.mkdir(TMP_DIR)

    download(output_path="/etc/jellyfin/media/SpotifyDL", processes=PROCESSES)

    # /etc/jellyfin/media/SpotifyDL
