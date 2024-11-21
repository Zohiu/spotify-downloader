import datetime
import os
import json

from dataclasses import dataclass

import traceback
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth


SPOTIPY_CLIENT_ID = "d2d972a5a37e4f3fb4e7666ddd267faa"
SPOTIPY_CLIENT_SECRET = "b4d045034f3b40269838a7f86e1545ae"
SPOTIPY_REDIRECT_URI = "http://127.0.0.1:9090"


@dataclass
class RawTrack:
    id: str
    name: str
    artists: list[str]
    album: str
    album_id: str
    added: str
    image_url: str
    track_number: int
    disc_number: int
    musicbrainz_id: str


@dataclass
class Playlist:
    name: str
    description: str
    size: int
    tracks: list[RawTrack]


def get_tracks_in_list(input_tracks: list):
    tracks = []
    for _track in input_tracks:
        if _track["track"]["id"] is None:
            continue
        tracks.append(RawTrack(
            id=_track["track"]["id"],
            name=_track["track"]["name"],
            artists=[artist["name"] for artist in _track["track"]["artists"]],
            album=_track["track"]["album"]["name"],
            album_id=_track["track"]["album"]["id"],
            added=_track["added_at"],
            image_url=_track["track"]["album"]["images"][0]["url"] if len(_track["track"]["album"]["images"]) > 0 else "",
            track_number=_track["track"]["track_number"],
            disc_number=_track["track"]["disc_number"],
            musicbrainz_id=""
        ))
    return tracks


class SpotifyUserData:
    def __init__(self, spotipy_client_id, spotipy_client_secret):
        self.auth_manager = SpotifyOAuth(client_id=spotipy_client_id, client_secret=spotipy_client_secret,
                                    redirect_uri="http://127.0.0.1:9090", scope='user-library-read')
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)

    def get_user_saved_tracks(self):
        saved_tracks = self.sp.current_user_saved_tracks(limit=1)
        playlist = Playlist(
            name="Liked Songs",
            description="",
            size=saved_tracks["total"],
            tracks=get_tracks_in_list(saved_tracks["items"])
        )

        while len(playlist.tracks) < playlist.size:
            playlist.tracks += get_tracks_in_list(
                self.sp.current_user_saved_tracks(offset=len(playlist.tracks), limit=50)["items"]
            )
            percentage = round(len(playlist.tracks) / playlist.size * 100)
            print(f"Loading {playlist.name} ({len(playlist.tracks)}/{playlist.size} - {percentage}%)")

        return playlist

    def get_user_playlists(self):
        playlists = []

        for _playlist in self.sp.current_user_playlists()["items"]:
            playlist = Playlist(
                name=_playlist["name"],
                description=_playlist["description"],
                size=_playlist["tracks"]["total"],
                tracks=[]
            )

            are_there_any_more_songs = 0
            while len(playlist.tracks) < playlist.size:
                playlist.tracks += get_tracks_in_list(
                    self.sp.playlist_items(_playlist["id"], offset=len(playlist.tracks), limit=50)["items"]
                )
                percentage = round(len(playlist.tracks) / playlist.size * 100)
                print(f"Loading {playlist.name} ({len(playlist.tracks)}/{playlist.size} - {percentage}%)")
                if len(playlist.tracks) == are_there_any_more_songs:
                    playlist.size = len(playlist.tracks)
                are_there_any_more_songs = len(playlist.tracks)

            playlists.append(playlist)

        return playlists

    def get_user_albums(self):
        albums = []

        for _album in self.sp.current_user_saved_albums()["items"]:
            album = Playlist(
                name=_album["album"]["name"],
                description="",
                size=_album["album"]["total_tracks"],
                tracks=[]
            )

            while len(album.tracks) < album.size:
                items: dict = self.sp.album_tracks(_album["album"]["id"], offset=len(album.tracks))["items"]
                for track in items:
                    track.update({"album": _album["album"]})
                album.tracks += get_tracks_in_list(
                    [{"track": track, "added_at": _album["album"]["release_date"] + "T00:00:00Z"} for track in items]
                )
                percentage = round(len(album.tracks) / album.size, 2) * 100
                print(f"Loading {album.name} ({len(album.tracks)}/{album.size} - {percentage}%)")

            albums.append(album)

        return albums

    def get_single_album(self, album_id):
        _album = self.sp.album(album_id)

        album = Playlist(
            name=_album["name"],
            description="",
            size=_album["total_tracks"],
            tracks=[]
        )

        while len(album.tracks) < album.size:
            items: dict = self.sp.album_tracks(_album["id"], offset=len(album.tracks))["items"]
            for item in items:
                item.update({"album": _album})
            album.tracks += get_tracks_in_list(
                [{"track": track, "added_at": _album["release_date"] + "T00:00:00Z"} for track in items]
            )
            percentage = round(len(album.tracks) / album.size, 2) * 100
            print(f"Loading {album.name} ({len(album.tracks)}/{album.size} - {percentage}%)")

        return album


def get_everything_in_library(spot):
    saved = spot.get_user_saved_tracks()
    playlists = spot.get_user_playlists()
    albums = spot.get_user_albums()

    total = [saved] + playlists + albums
    outlist = []

    for playlist in total:
        tracks = []

        for track in playlist.tracks:
            tracks.append(
                {
                    "id": track.id,
                    "added": track.added,
                    "name": track.name,
                    "artists": track.artists,
                    "album": track.album,
                    "image_url": track.image_url,
                    "track_number": track.track_number,
                    "disc_number": track.disc_number
                }
            )

        outlist.append(
            {
                "name": playlist.name,
                "description": playlist.description,
                "tracks": tracks
            }
        )

        with open("spotify-playlists.json", "w") as f:
            f.write(json.dumps(outlist, indent=2))


def get_single_album(spot, album_id):
    album = spot.get_single_album(album_id)

    outlist = []
    tracks = []

    for track in album.tracks:
        tracks.append(
            {
                "id": track.id,
                "added": track.added,
                "name": track.name,
                "artists": track.artists,
                "album": track.album,
                "image_url": track.image_url,
                "track_number": track.track_number,
                "disc_number": track.disc_number
            }
        )

    outlist.append(
        {
            "name": album.name,
            "description": "",
            "tracks": tracks
        }
    )

    return outlist

    # with open("spotify-playlists.json", "w") as f:
    #     f.write(json.dumps(outlist, indent=2))


def get_every_album_of_everything_in_library(spot):
    print("get albums")
    albums = spot.get_user_albums()
    print("get liked songs")
    saved = spot.get_user_saved_tracks()
    print("get playlists")
    playlists = spot.get_user_playlists()

    total = [saved] + playlists
    outlist = []
    print("Adding albums")
    for album in albums:
        tracks = []

        for track in album.tracks:
            tracks.append(
                {
                    "id": track.id,
                    "added": track.added,
                    "name": track.name,
                    "artists": track.artists,
                    "album": track.album,
                    "image_url": track.image_url,
                    "track_number": track.track_number,
                    "disc_number": track.disc_number
                }
            )

        outlist.append(
            {
                "name": album.name,
                "description": album.description,
                "tracks": tracks
            }
        )

    print("Searching more albums")
    total_progress = 0
    for playlist in total:
        total_progress += 1
        print(f"Total progress: {total_progress}/{len(total)}")

        progress = 0
        for track in playlist.tracks:
            progress += 1
            for already_existing in outlist:
                if already_existing["name"] == track.album:
                    break

            else:
                print(f"Playlist {playlist.name} - Progress: {progress}/{len(playlist.tracks)}")
                track_album = get_single_album(spot, track.album_id)

                tracks = []
                for subtrack in track_album[0]["tracks"]:
                    tracks.append(
                        {
                            "id": subtrack["id"],
                            "added": subtrack["added"],
                            "name": subtrack["name"],
                            "artists": subtrack["artists"],
                            "album": subtrack["album"],
                            "image_url": subtrack["image_url"],
                            "track_number": subtrack["track_number"],
                            "disc_number": subtrack["disc_number"]
                        }
                    )
                outlist.append(
                    {
                        "name": track.album,
                        "description": track_album[0]["description"],
                        "tracks": tracks
                    }
                )

    with open("spotify-playlists.json", "w") as f:
        f.write(json.dumps(outlist, indent=2))


if __name__ == "__main__":
    spot = SpotifyUserData(SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET)

    # link = "https://open.spotify.com/album/0zAswLWCBWrzJjqy07cJmA?si=VQrYITmnSN6NhWLgB-SfXQ"

    # get_single_album(spot, link.split("https://open.spotify.com/album/")[1].split("?si=")[0])

    print(get_every_album_of_everything_in_library(spot))