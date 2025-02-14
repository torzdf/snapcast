#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" A simple control script for SnapCast Server to obtain meta information from MPD """

# pylint:disable=too-many-lines


from __future__ import annotations

import json
import logging
import os
import socket
import sys
import typing as T

from base64 import b64encode
from dataclasses import dataclass, field
from getopt import getopt, GetoptError
from queue import Queue, Empty as QueueEmpty
from select import select
from threading import Event, Thread
from time import sleep, time

__version__ = "0.9.0"


@dataclass
class _SnapData:
    """ Parent dataclass for providing SnapCast formatted data to the server. Should not be used
    directly """
    _defaults: dict[str, T.Any] = field(init=False, default_factory=dict)
    """ dict[str, Any]: The default values for the data class """

    def __post_init__(self) -> None:
        """ Store the initial default values for resetting """
        self._defaults = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def _snake_to_camel_case(cls, key: str) -> str:
        """ Convert a snake case formatted string to lower camel case format

        Parameters
        ----------
        key : str
            The key, in snake case, to be converted

        Returns
        -------
        str
            The key formatted to lower camel case
        """
        caps = "".join(x.title() for x in key.lower().split("_"))
        retval = caps[0].lower() + caps[1:]
        return retval

    def _to_dict(self) -> dict[str, T.Any]:
        """ Return the dataclass in a dictionary formatted for conversion to SnapServer jsonrpc

        Returns
        --------
        dict[str, Any]
            The dataclass properties serialized into a Python dictionary
        """
        return {self._snake_to_camel_case(k): v
                for k, v in self.__dict__.items()
                if not k.startswith("_")}

    @property
    def rpc_data(self) -> dict[str, T.Any]:
        """ The object's currently stored data, formatted ready for jsonrpc serialization

        Returns
        -------
        Dict[str, Any]
            The current status of the object ready for jsonrpc serialization
        """
        retval = {
            k: v for k, v in self._to_dict().items()
            if not (isinstance(v, (int, float)) and v < 0)  # Skip negative defaults
            and not (isinstance(v, (list, dict, str)) and not v)}  # Skip non-existant values
        return retval

    def reset(self) -> None:
        """ Reset all values to their initial values """
        logger.debug("Resetting to initial values: %s", self.__class__.__name__)
        for k, v in self._defaults.items():
            setattr(self, k, v)


@dataclass
class SnapProperties(_SnapData):  # pylint:disable=too-many-instance-attributes
    """ Holds the properties for the plugin source and provides methods for outputting to
    :class:`Interface` in a format that can be used for jsonrpc transmission

    Reference
    ---------
    https://github.com/badaix/snapcast/blob/develop/doc/json_rpc_api/stream_plugin.md#pluginstreamplayergetproperties
    """  # noqa:E501 # pylint:disable=line-too-long
    playback_status: T.Literal["playing", "paused", "stopped", ""] = ""
    """ Literal["playing", "paused", "stopped"] : The current playback status """
    loop_status: T.Literal["none", "track", "playlist", ""] = ""
    """ Literal["none", "track", "playlist"] : The current repeat status """
    shuffle: bool = False
    """ bool : Traverse through the playlist in random order """
    volume: int = -1
    """ int : Volume in percent, valid range [0..100] """
    mute: bool = False
    """ bool : Current mute state """
    rate: float = -1.0
    """ float : The current playback rate, valid range (0..) """
    position: float = -1.0
    """ float : Current playback position in seconds """
    can_go_next: bool = False
    """ bool : Whether the client can call the next method on this interface and expect the current
    track to change """
    can_go_previous: bool = False
    """ bool : Whether the client can call the previous method on this interface and expect the
    current track to change """
    can_play: bool = False
    """ bool : Whether playback can be started using play or playPause """
    can_pause: bool = False
    """ bool : Whether playback can be paused using pause or playPause """
    can_seek: bool = False
    """ bool : Whether the client can control the playback position using seek and setPosition """
    can_control: bool = False
    """ bool : Whether the media player may be controlled over this interface """


@dataclass
class SnapMetadata(_SnapData):  # pylint:disable=too-many-instance-attributes
    """ Holds the metadata for the currently playing track and provides methods for outputting
    to SnapCast in json format

    Reference: https://github.com/badaix/snapcast/blob/develop/doc/json_rpc_api/stream_plugin.md#pluginstreamplayergetproperties
    """  # noqa:E501 # pylint:disable=line-too-long
    track_id: str = ""
    """ str : A unique identity for this track within the context of an MPRIS object
    (eg: tracklist) """
    duration: float = -1.0
    """ float : The duration of the song in seconds; may contain a fractional part """
    artist: list[str] = field(default_factory=list)
    """ list[str] : The track artist(s) """
    artist_sort: list[str] = field(default_factory=list)
    """ list[str] : Same as :attr:`artist`, but for sorting. This usually omits prefixes such as
    'The' """
    album: str = ""
    """ str : The album name """
    album_sort: str = ""
    """ str : Same as :attr:`album`, but for sorting """
    album_artist: list[str] = field(default_factory=list)
    """ list[str] : The album artist(s) """
    album_artist_sort: list[str] = field(default_factory=list)
    """ list[str] : Same as :attr:`album_artist`, but for sorting """
    name: str = ""
    """ str : A name for this song. This is not the song title. The exact meaning of this tag is
    not well-defined. It is often used by badly configured internet radio stations with broken
    tags to squeeze both the artist name and the song title in one tag """
    date: str = ""
    """ str : The song's release date. This is usually a 4-digit year """
    original_date: str = ""
    """ str : The song's original release date """
    composer: list[str] = field(default_factory=list)
    """ The composer(s) of the track """
    performer: str = ""
    """ str : The artist who performed the song """
    conductor: str = ""
    """ str : The conductor who conducted the song """
    work: str = ""
    """ str: A work is a distinct intellectual or artistic creation, which can be expressed in the
    form of one or more audio recordings """
    grouping: str = ""
    """ str: 'used if the sound belongs to a larger category of sounds/music' (from the IDv2.4.0
    TIT1 description) """
    comment: list[str] = field(default_factory=list)
    """ list[str] : A (list of) free-form comment(s) """
    label: str = ""
    """ str : The name of the label or publisher """
    musicbrainz_artist_id: str = ""
    """ str : The artist id in the MusicBrainz database """
    musicbrainz_album_id: str = ""
    """ str : The album id in the MusicBrainz database """
    musicbrainz_album_artist_id: str = ""
    """ str : The album artist id in the MusicBrainz database """
    musicbrainz_track_id: str = ""
    """ str : The track id in the MusicBrainz database """
    musicbrainz_release_track_id: str = ""
    """ str : The release track id in the MusicBrainz database """
    musicbrainz_work_id: str = ""
    """ str: The work id in the MusicBrainz database"""
    lyrics: list[str] = field(default_factory=list)
    """ list[str] : The lyrics of the track """
    bpm: int = -1
    """ int : The speed of the music, in beats per minute """
    auto_rating: float = -1.0
    """ float : An automatically-generated rating, based on things such as how often it has been
    played. This should be in the range 0.0 to 1.0 """
    content_created: str = ""
    """ str : Date/Time: When the track was created. Usually only the year component will be
    useful. """
    disc_number: int = -1
    """ int : The disc number on the album that this track is from """
    first_used: str = ""
    """ str : Date/Time: When the track was first played """
    genre: list[str] = field(default_factory=list)
    """ list[str] : The genre(s) of the track """
    last_used: str = ""
    """ str: Date/Time: When the track was last played """
    lyricist: list[str] = field(default_factory=list)
    """ list[str] : The lyricist(s) of the track """
    title: str = ""
    """ str: The track title """
    track_number: int = -1
    """ int: The track number on the album disc """
    url: str = ""
    """ str : [uri] The location of the media file """
    art_url: str = ""
    """ str: [uri] The location of an image representing the track or album. Clients should not
    assume this will continue to exist when the media player stops giving out the URL """
    art_data: dict[T.Literal["data", "extension"], str] = field(default_factory=dict)
    """ dict[T.Literal["data", "extension"], str] : If :attr:`art_url` is not supplied, then
    SnapServer will decode the Base64 encoded image representing the track or album stored in
    `data`, cache the image and will publish the image via art_url """
    use_count: int = -1
    """int : The number of times the track has been played """
    user_rating: float = -1.0
    """ float : A user-specified rating. This should be in the range 0.0 to 1.0 """
    spotify_artist_id: str = ""
    """ str : The Spotify Artist ID """
    spotify_track_id: str = ""
    """ str : The Spotify Track ID """

    def __repr__(self) -> str:
        """ Pretty print representation, preventing encoded album art from spamming log """
        params: dict[str, T.Any] = {k: f"'{v}'" if isinstance(v, str) else v
                                    for k, v in self.__dict__.items()
                                    if not k.startswith("_")}
        art_data: dict[str, str] = params["art_data"]
        if art_data.get("data"):
            pretty_art = art_data.copy()
            pretty_art["data"] = f"<length={len(pretty_art['data'])}>"
            params["art_data"] = pretty_art
        return f"{self.__class__.__name__}({', '.join(f'{k}={params[k]}' for k in params)}"


class CliArgs:  # pylint:disable=too-many-instance-attributes
    """ Handles and stores the passed in command line arguments"""
    def __init__(self) -> None:
        self.mpd_host: str = "localhost"
        self.mpd_port: int = 6600
        self.mpd_password: T.Optional[str] = None
        self.mpd_requery: int = 15
        self.snapcast_host: str = "localhost"
        self.snapcast_port: int = 1780
        self.stream: str = "default"
        self.debug: bool = False
        self.log_dir: str = ""

        self._parse_args()

    def __repr__(self) -> str:
        """ Pretty string representation of the :class:`CliArgs` object, masking the password """
        params = {k:  "******" if k == "mpd_password" and v is not None else v
                  for k, v in self.__dict__.items()}
        return f"{self.__class__.__name__}{params}"

    @property
    def _meta(self) -> dict[str, tuple[str, str]]:
        """ Meta information about the command line options in the order that they should appear
        in usage output

        Returns
        -------
        dict[str, tuple[str, str]]
            Key is the command line argument. Value contains help text in the first position and
            either a meta representation of the accepted value for the key (for key=value
            arguments) or the accepted abbreviated argument (for switches)
        """
        return {"help": ("Show this help message", "-h"),
                "mpd-host": ("Set the mpd server address", "ADDR"),
                "mpd-port": ("Set the TCP port", "PORT"),
                "mpd-query": ("How often to query MPD for updated data", "SECONDS"),
                "snapcast-host": ("Set the snapcast server address", "ADDR"),
                "snapcast-port": ("Set the snapcast server port", "PORT"),
                "stream": ("Set the stream id", "ID"),
                "debug": ("Run in debug mode", "-d"),
                "log-dir": ("Path to folder to output optionally output a log file", "PATH"),
                "version": ("meta_mpd version", "-v")}

    @property
    def valid_switches(self) -> str:
        """ str : Concatenated list of valid command line switches"""
        return "".join([v[1][1] for v in self._meta.values() if v[1].startswith("-")])

    @property
    def valid_arguments(self) -> list[str]:
        """ list[str] List of valid command line arguments """
        return [f"{k.replace('_', '-')}{'=' if not v[1].startswith('-') else ''}"
                for k, v in self._meta.items()]

    @property
    def usage(self) -> str:
        """ Pretty printed usage information

        Returns
        -------
        str
            Usage information for outputting to console on error or when `--help` is passed
        """
        switches = {f"    {v[1]}, --{k}": v[0]
                    for k, v in self._meta.items() if v[1].startswith("-")}
        arguments = {f"    --{k}={v[1]}": v[0]
                     for k, v in self._meta.items() if not v[1].startswith("-")}
        padding = max(len(k) for k in list(switches) + list(arguments)) + 3

        retval = (f"Usage: {sys.argv[0]} [OPTION]\n\n" +
                  "\n".join(k.ljust(padding) + v for k, v in arguments.items()) + "\n\n" +
                  "\n".join(k.ljust(padding) + v for k, v in switches.items()) + "\n\n" +
                  "Report bugs to https://github.com/badaix/snapcast/issues")
        return retval

    def _parse_args(self) -> None:
        """ Parse the cli arguments and populate the :class:`CliArgs` object's properties """
        try:
            opts, cli_args = getopt(sys.argv[1:], self.valid_switches, self.valid_arguments)
        except GetoptError as ex:
            msg = ex.args[0]
            print(f"{sys.argv[0]}: {msg}", file=sys.stderr)
            print(file=sys.stderr)
            print(self.usage)
            sys.exit(2)

        if cli_args:
            print(self.usage)
            sys.exit()

        for opt, arg in opts:
            if opt in ("-h", "--help"):
                print(self.usage)
                sys.exit()
            elif opt in ("-v", "--version"):
                print(f"meta_mpd version: {__version__}")
                sys.exit()
            elif opt in ("-d", "--debug"):
                self.debug = True
            else:
                prop = opt.lstrip("-").replace("-", "_")
                if prop not in self.__dict__:
                    raise ValueError(f"Property '{prop}' for option '{opt}' does "
                                     f"not exist in {list(self.__dict__)}")
                setattr(self, prop, type(getattr(self, prop))(arg))

        if "@" in self.mpd_host:
            self.mpd_password, self.mpd_host = self.mpd_host.split("@", maxsplit=1)
        if self.log_dir:
            self.log_dir = os.path.abspath(os.path.expanduser(self.log_dir))


def _configure_logging(is_debug: bool, log_path: str) -> logging.Logger:
    """ Set up the logger

    Parameters
    ----------
    is_debug : bool
        ``True`` for debug logging. ``False`` for info logging

    Returns
    -------
    :class:`logging.Logger`
        The configured Logger object
    """
    log_level = logging.DEBUG if is_debug else logging.INFO
    log_format = logging.Formatter("%(asctime)s %(threadName)-10s %(module)s %(funcName)-20s "
                                   "%(levelname)s: %(message)s")

    log_handler = logging.StreamHandler()
    log_handler.setFormatter(log_format)

    control_logger = logging.getLogger(__name__)
    control_logger.propagate = False
    control_logger.setLevel(log_level)
    control_logger.addHandler(log_handler)

    if log_path:
        if os.path.isdir(log_path):
            filename = os.path.join(log_path, "meta_mpd.log")
            file_handler = logging.FileHandler(filename=filename, mode="w")
            file_handler.setFormatter(log_format)
            control_logger.addHandler(file_handler)
        else:
            print(f"WARNING: Log folder '{log_path}' does not exist. Not generating log file",
                  file=sys.stderr)

    return control_logger


class MPDData:
    """ Handles the updating of :class:`SnapProperties` and :class:`SnapMetadata` from the MPD
    Protocol

    Parameters
    ----------
        mpd : :class:`MPD`
            The interface for communicating with the Music Player Daemon
    """
    def __init__(self, mpd: MPD) -> None:
        self._mpd = mpd
        self._properties = SnapProperties(rate=1.0,  # MPD has no rate control
                                          can_go_next=True,
                                          can_go_previous=True,
                                          can_play=True,
                                          can_pause=True,
                                          can_control=True)
        self._metadata = SnapMetadata()
        self._current_uri = ""

        tags: list[tuple[str, str] | str] = [
            "artist",
            ("artistsort", "artist_sort"),
            "album",
            ("albumsort", "album_sort"),
            ("albumartist", "album_artist"),
            ("albumartistsort", "album_artist_sort"),
            "title",
            ("track", "track_number"),
            "name", "genre", "date",
            ("originaldate", "original_date"),
            ("date", "content_created"),
            ("disc", "disc_number"),
            "composer", "performer", "work", "grouping", "comment", "label",
            ("musicbrainz_artistid", "musicbrainz_artist_id"),
            ("musicbrainz_albumid", "musicbrainz_album_id"),
            ("musicbrainz_albumartistid", "musicbrainz_album_artist_id"),
            ("musicbrainz_trackid", "musicbrainz_track_id"),
            ("musicbrainz_releasetrackid", "musicbrainz_release_track_id"),
            ("musicbrainz_workid", "musicbrainz_work_id")]
        self._mapping = tags + [("id", "track_id"), ("file", "url"), "duration"]
        """ list[tuple[str, str] | str] : Mapping of MPD keys to SnapMetadata attributes. Tuples
        have the MPD key in the first position, Snap in the second. Strings are the same value for
        both MPD and Snap """
        # Unmapped from MPD: ['format', 'last-modified', 'pos', 'time']
        # Unmapped from SnapCast: ['art_url', 'auto_rating', 'bpm', 'conductor', 'first_used',
        #                          'last_used', 'lyricist', 'lyrics', 'spotify_artist_id',
        #                          'spotify_track_id', 'use_count', 'user_rating']

    def _update_properties(self) -> str | None:
        """ Update the :attr:`_properties` from the current MPD `status` command

        Returns
        -------
        str | None
            ``None`` on successful update. `str` containing an error message on failure
        """
        data = self._mpd.communicate("status", as_dict=True)
        if isinstance(data, str):
            return data
        state = data["state"]
        self._properties.playback_status = T.cast(
            T.Literal["playing", "paused", "stopped"],
            state + {"play": "ing", "pause": "d", "stop": "ped"}[state])
        self._properties.loop_status = T.cast(
            T.Literal["none", "track", "playlist"],
            {"0": "none", "1": "track", "2": 'playlist'}[data["repeat"]])
        self._properties.shuffle = bool(int(data["random"]))
        self._properties.volume = int(data["volume"])
        self._properties.position = float(data.get("elapsed", -1.0))
        self._properties.mute = int(data["volume"]) == 0  # No explicit mute control
        self._properties.can_seek = "duration" in data
        return None

    def current_properties(self) -> SnapProperties | str:
        """ Obtain the current properties (status) from MPD

        Returns
        --------
        :class:`SnapProperties` | str
            The current populated SnapCast properties object on success. A `str` containing the
            error message on failure
        """
        self._mpd.set_idle(False)
        result = self._update_properties()
        if isinstance(result, str):
            return result
        return self._properties

    def _image_type_from_header(self, image: bytes) -> str:
        """ Obtain the type of image from it's header

        Reference
        ---------
        https://www.garykessler.net/library/file_sigs.html

        Parameters
        ----------
        image : bytes
            The image to obtain the image type from

        Returns
        -------
        str
            The image type. Empty string if could not be found
        """
        retval = ""
        lookup = {b"\xff\xd8": (0, "jpg"),
                  b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a": (0, "png"),
                  b"\x57\x45\x42\x50": (8, "webp"),
                  b"\x42\x4d": (0, "bmp"),
                  b"\x49\x49\x2a\x00": (0, "tif")}
        for key, val in lookup.items():
            if key == image[val[0]:val[0] + len(key)]:
                retval = val[1]
                logger.debug(  # pylint:disable=E0606
                    "Identified image type as '%s' from header bytes %s", val[1], key)
                break

        return retval

    def _get_artwork(self, uri: str) -> None:
        """ Update the :attr:`_metadata` for the artwork of the currently playing track.

        Try to get the image from the ID3 tag first. If none available, try from the filesystem

        Parameters
        ----------
        uri : str
            The URI of the song to obtain the album art for
        """
        image_type: str | None = ""
        for command in ("readpicture", "albumart"):
            data = self._mpd.get_binary_data(f'{command} "{uri}"')

            if isinstance(data, str):
                logger.debug("Connection error obtaining artwork: %s", data)
                continue

            image, text = data
            if len(image) == 0:
                continue

            image_type = next((x for x in text if x.startswith("type:")), None)
            image_type = self._image_type_from_header(image) if image_type is None else image_type
            if not image_type:
                logger.debug("No image type found for command: '%s', uri: '%s'", command, uri)
                continue
            break

        if not image_type:
            logger.debug("Could not determine image type. Not storing album art")
            return

        image_type = image_type.replace("type: image/", "")
        # Ref: https://www.freeformatter.com/mime-types-list.html#mime-types-list
        im_types = {"svg+xml": "svg", "jpeg": "jpg"}
        ext = im_types.get(image_type, image_type)
        data = b64encode(image).decode("utf-8")

        logger.debug("Stored base64 album art of length %s for extension '%s'", len(data), ext)
        self._metadata.art_data = {"data": data, "extension": ext}

    def _populate_metadata(self, song: dict[str, str]) -> None:
        """ Populate :attr:`_metadata` with the latest song information obtained from MPD

        Parameters
        ----------
        song: dict[str, str]
            The latest song information obtained from MPD
        """
        self._metadata.reset()
        for lookup in self._mapping:
            if isinstance(lookup, tuple):
                mpd, snap = lookup
            else:
                mpd = snap = lookup
            if mpd not in song:
                logger.debug("Skipping %s(%s) as not in MPD data", mpd, snap)
                continue

            old_attr = getattr(self._metadata, snap)
            mpd_attr = song.get(mpd, old_attr)

            if not isinstance(old_attr, (list, float, int, str)):
                raise NotImplementedError(f"Type handling for {type(old_attr)} not implemented")
            if isinstance(old_attr, list):
                new_attr: list[str] | float | int | str = [mpd_attr]
            else:
                new_attr = type(old_attr)(mpd_attr)
            logger.debug("MPD: '%s', Snap: '%s', value: '%s'", mpd, snap, new_attr)

            setattr(self._metadata, snap, new_attr)

    def _update_metadata(self) -> str | None:
        """ Update the :attr:`_metadata` from the MPD `currentsong` command

        Reference
        ---------
        https://mpd.readthedocs.io/en/latest/protocol.html#tags

        Returns
        -------
        str | None
            ``None`` on successful update. `str` containing an error message on failure
        """
        song = self._mpd.communicate("currentsong", as_dict=True)
        if isinstance(song, str):
            return song

        logger.debug("Got information from MPD: %s", song)
        uri = song.get("file", "")
        if uri == self._current_uri:
            logger.debug("Track has not changed")
            return None

        self._current_uri = uri
        self._populate_metadata(song)

        if uri:
            self._get_artwork(uri)
        return None

    def current_metadata(self) -> SnapMetadata | str:
        """ Obtain the current track's metadata from MPD

        Returns
        -------
        :class:`SnapMetadata` | str
            The current SnapCast metadata object or `str` containing the error message on failure
        """
        result = self._update_metadata()
        self._mpd.set_idle(True)

        if isinstance(result, str):
            return result
        return self._metadata


class MPDControl:
    """ Handles controlling MPD from received jsonrpc requests

    Parameters
    ----------
    mpd : :class:`MPD`
        The interface for communicating with the Music Player Daemon
    """
    def __init__(self, mpd: MPD) -> None:
        self._mpd = mpd
        self._last_volume = -1

    def _get_volume(self) -> int | str:
        """ Obtain the currently set volume

        Returns
        -------
        int | str
            The currently set volume (int) or the error message (str) on failure
        """
        data = self._mpd.communicate("getvol", as_dict=True)
        if isinstance(data, str):
            return data
        return int(data["volume"])

    def _send_command(self, command: str | list[str]) -> str | None:
        """ Send a control command to MPD and check the return status. If an error, return the
        message

        Parameters
        ----------
        command : str | list[str]
            The control command or list of commands to send to MPD

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        logger.debug("Got '%s' control", command)
        result = self._mpd.communicate(command)
        retval = result if isinstance(result, str) else None
        return retval

    def play(self) -> str | None:
        """ Start playback

        Returns
        -------
        str | None
           ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command("play")

    def pause(self) -> str | None:
        """ Pause playback

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command("pause 1")

    def play_pause(self) -> str | None:
        """ Toggle play/pause

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command("pause")

    def stop(self) -> str | None:
        """ Stop playback

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command("stop")

    def next(self) -> str | None:
        """ Skip to the next track

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command("next")

    def previous(self) -> str | None:
        """ Skip to the previous track

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command("previous")

    def seek(self, offset: float) -> str | None:
        """ Seek forwards or backwards from the current position

        Parameters
        ----------
        offset : float
            The seek offset, in seconds, from the current position

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        val = str(offset) if offset < 0 else f"+{offset}"
        return self._send_command(f"seekcur {val}")

    def set_position(self, position: float) -> str | None:
        """ Set the absolute track position

        Parameters
        ----------
        position : float
            The position, in seconds, to set for the current track

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command(f"seekcur {position}")

    def set_loop_status(self, status: T.Literal["none", "track", "playlist"]) -> str | None:
        """ Set the current loop status

        Parameters
        -----------
        status: Literal["none", "track", "playlist"]
            `"none"` to disable looping, `"track"` to loop the current track, `"playlist"` to loop
            the entire playlist

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        if status == "none":
            return self._send_command("repeat 0")
        if status == "track":
            return self._send_command(["single 1", "repeat 1"])
        if status == "playlist":
            return self._send_command(["single 0", "repeat 1"])
        return None

    def set_shuffle(self, shuffle: bool) -> str | None:
        """ Enable or disable playlist shuffle status

        Parameters
        ----------
        shuffle : bool
            ``True`` to enable shuffle. ``False`` to disable

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        return self._send_command(f"random {int(shuffle)}")

    def set_volume(self, volume: int) -> str | None:
        """ Set the volume

        Parameters
        ----------
        volume : int
            The percent to set the volume to

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        retval = self._send_command(f"setvol {volume}")
        if retval is None:
            self._last_volume = volume
        return retval

    def set_mute(self, mute: bool) -> str | None:
        """ Enable or disable mute. Note: MPD has no explicit mute control, so some bobbing and
        weaving is required

        Parameters
        ----------
        mute : bool
            ``True`` to mute. ``False`` to disable mute

        Returns
        -------
        str | None
            ``None`` if the command executed successfully otherwise the error message on failure
        """
        current_volume = self._get_volume()
        if isinstance(current_volume, str):  # We errored
            return current_volume

        if self._last_volume < 0:  # Whether muting or un-muting we need to set initial last volume
            # Set sane default to not blow up anyone's speakers, if we have started muted
            self._last_volume = current_volume if current_volume > 0 else 40

        if mute:
            # Don't overwrite last volume if we are already muted
            self._last_volume = current_volume if current_volume > 0 else self._last_volume
            return self._send_command("setvol 0")

        # If we are already un-muted don't change the volume
        un_mute_volume = current_volume if current_volume > 0 else self._last_volume
        return self._send_command(f"setvol {un_mute_volume}")


class MPDIdle:
    """ Monitors MPD for status changes and notifies

    Parameters
    ----------
    mpd : :class:`MPD`
        The interface for communicating with the Music Player Daemon
    """

    def __init__(self, mpd: MPD) -> None:
        self._mpd = mpd
        self._can_idle = Event()
        self._is_idling = Event()
        self._update_available = Event()
        self._subsystems = " ".join(["player", "playlist", "mixer", "options"])
        self._thread = Thread(target=self._monitor,
                              name="mpd_idle",
                              args=(mpd, self._can_idle, self._is_idling, self._update_available))
        self._thread.start()

    @property
    def is_idling(self) -> bool:
        """ bool : ``True`` If MPD currently has the socket open for an `idle` command otherwise
        ``False`` """
        return self._is_idling.is_set()

    @property
    def update_available(self) -> bool:
        """ bool : ``True`` if MPD has had a state change """
        return self._update_available.is_set()

    def set_idle(self, state: bool) -> None:
        """ Enable or disable MPD Idle mode to monitor for status updates

        Parameters
        ----------
        state : bool
            ``True`` to enable MPD idle mode. ``False`` to disable it
        """
        if state == self.is_idling:
            logger.debug("MPD idle state requested: %s. Current state is %s. Nothing to do")
            return

        logger.debug("Setting MPD idle to %s", state)
        if state:
            self._mpd.communicate(f"idle {self._subsystems}")
            self._can_idle.set()
            self._is_idling.wait()
            logger.debug("MPD is in idle mode")
        else:
            self._can_idle.clear()
            while True:
                if not self._is_idling.is_set():
                    break
                sleep(0.2)
            self._mpd.communicate("noidle")
            logger.debug("MPD has exited idle mode")

    @staticmethod
    def _monitor(mpd: MPD, can_idle: Event, is_idling: Event, update: Event) -> None:
        """ Monitor MPD for updates when in idle mode. If an update received, set the Event and
        exit the thread

        Parameters
        ----------
        mpd : :class:`MPD`
            The interface for communicating with the Music Player Daemon
        can_idle : :class:`threading.Event`
            The even that indicates whether MPD is permitted to be in idle mode
        is_idling : :class:`threading.Event`
            The event that indicates that `idle` currently holds the socket open, meaning that it
            is not available for other requests
        update : :class:`threading.Event`
            The event to set when MPD indicates that an update is available
        """
        logger.debug("Starting MPD idle monitor")
        while True:
            if not can_idle.is_set() and is_idling.is_set():
                logger.debug("Exiting MPD idle mode")
                is_idling.clear()

            can_idle.wait()

            if not is_idling.is_set():
                logger.debug("Entering MPD idle mode")
                update.clear()
                is_idling.set()
            if mpd.data_available:
                result = mpd.communicate("")  # Clear the return result
                logger.debug("MPD update received through idle: %s", result)
                update.set()


class MPD:  # pylint:disable=too-many-instance-attributes
    """ A simple socket interface to MPD

    Parameters
    ----------
    host : str
        The host address of MPD
    port : int
        The port to access MPD on
    password : str | None
        The password to access MPD. ``None`` for no password
    timeout : float, optional
        Amount of time, in seconds, before timing out the connection. Default: 5.0
    retries : int, optional
        The number of retries to either error out (on initial connection) or return an error
        (during normal running)
    """
    def __init__(self,  # pylint:disable=too-many-positional-arguments,too-many-arguments
                 host: str,
                 port: int,
                 password: str | None,
                 timeout: float = 5.0,
                 retries: int = 5
                 ) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._initial_timeout = timeout

        self._retry_count = retries

        self._status = "Not initialized"
        self._binary_prefix = "binary:"

        self._idle = MPDIdle(self)
        self._read_buffer: bytes = b""
        sock = self._connect()
        if isinstance(sock, str):
            self._status = sock
            return

        self._sock = sock
        self._init_connection(first_connect=True)

        data = MPDData(self)
        control = MPDControl(self)

        self.set_idle = self._idle.set_idle
        self.get_properties = data.current_properties
        self.get_metadata = data.current_metadata

        self.play = control.play
        self.pause = control.pause
        self.play_pause = control.play_pause
        self.stop = control.stop
        self.next = control.next
        self.previous = control.previous
        self.seek = control.seek
        self.set_position = control.set_position
        self.set_loop_status = control.set_loop_status
        self.set_shuffle = control.set_shuffle
        self.set_volume = control.set_volume
        self.set_mute = control.set_mute

    @classmethod
    def _list_to_dict(cls, mpd_list: list[str]) -> dict[str, str]:
        """ Parse a multi-line listed output of 'key: value' pair strings from MPD and convert to
        a dictionary. All keys are lowercased for consistency and future proofing.

        Parameters
        ----------
        mpd_list : list[str]
            A list of lines output from MPD

        Returns
        -------
        dict[str, str]
            The output converted to a python dictionary
        """
        split_list = [line.split(":", maxsplit=1) for line in mpd_list]
        return {line[0].strip().lower(): line[1].strip() for line in split_list}

    @property
    def idle(self) -> MPDIdle:
        """ :class:`MPDIdle` : The object responsible for monitoring MPD for status changes """
        return self._idle

    @property
    def status(self) -> T.Literal["Ready"] | str:
        """ Literal["Ready"] | str : `"Ready"` if MPD has been connected to and is ready to receive
        commands, otherwise a string indicating what the failure is for upstream logging """
        return self._status

    @property
    def update_available(self) -> bool:
        """ bool : ``True`` if MPD has had a state change """
        return self._idle.update_available

    @property
    def data_available(self) -> bool:
        """ bool : ``True`` if data is available to be read from MPD otherwise ``False``. Used for
        idle mode """
        read_socket, _, _ = select([self._sock], [], [], 1)
        return bool(read_socket)

    def _connect(self) -> socket.socket | str:
        """ Connect to MPD over TCP and return the socket

        Returns
        -------
        :class:`socket.socket` | str
            The socket object if successfully connected. A `str` indicating the error on failure
        """
        logger.debug("Connecting to MPD")
        flags = [socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP]
        flags.append(socket.AI_ADDRCONFIG if hasattr(socket, "AI_ADDRCONFIG") else 0)

        err_msg = "Error connecting to MPD"
        attempt = 0
        while True:
            try:
                sock = None
                for result in socket.getaddrinfo(self._host, self._port, *flags):
                    sock = socket.socket(*result[:3])
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    sock.settimeout(self._timeout)
                    sock.connect(result[-1])
                    logger.debug("Connected to MPD: %s", sock)
                    return sock
            except (socket.timeout, OSError) as err:
                msg = f"{err.__class__.__name__}: {str(err)}"
                if attempt == self._retry_count:
                    logger.warning("%s. Giving up after %s retries", msg, self._retry_count)
                    if sock is not None:
                        sock.close()
                    return f"{err_msg}: {msg}"
                attempt += 1
                logger.warning("%s. Retrying %s of %s", msg, attempt, self._retry_count)
                sleep(self._timeout)

            if sock is None:
                return err_msg

    def _write_line(self, line: str) -> None:
        """ Write a line to the MPD TCP socket

        Parameters
        -----------
        line : str
            The line to write to MPD
        """
        log_line = "password(******)" if line.split()[0] == "password" else line
        logger.debug("Sending to MPD: '%s'", log_line)
        self._sock.sendall(f"{line}\n".encode("utf-8"))

    def _get_bytes(self, size, is_exact: bool = False) -> bytes:
        """ Obtain raw bytes from MPD

        Parameters
        ----------
        size: int
            The number of bytes to retrieve
        is_exact: bool, optional
            If ``True`` then read exactly the number of bytes given by `size`. If ``False`` then
            read up to the number of bytes given by `size`. Default: ``False``

        Returns
        -------
        bytes:
            The retrieved bytes from MPD

        Raises
        ------
        ConnectionError
            If connection has been lost
        """
        retval = self._read_buffer[:size]
        self._read_buffer = self._read_buffer[size:]
        if len(retval) == size or retval.endswith((b"OK\n", b"list_OK\n")):
            return retval

        # TODO also try/reconnect may be able to go here too
        remainder = size - len(retval)
        while remainder > 0:
            recv = self._sock.recv(remainder)
            if not recv:
                raise ConnectionError
            retval += recv
            if not is_exact:
                break
            remainder -= len(recv)

        return retval

    def _read_line(self, size: int = 1024) -> T.Generator[str, None, None]:
        """ Read a line from the socket, decode it and return as a string

        Parameters
        ----------
        size: int, optional
            The number of bytes to read from the socket. Default: 1024

        Yields
        ------
        str
            A cleansed line read from the MPD TCP socket
        """
        read = b""
        while True:
            while b"\n" not in read:
                recv = self._get_bytes(size)
                read += recv

            read, overflow = read.split(b"\n", maxsplit=1)
            self._read_buffer += overflow
            line = read.decode("utf-8").strip()
            read = b""

            logger.debug("Read from MPD: '%s'", line)

            yield line

    def _read_lines(self, last_line_prefix: str | None = None) -> list[str] | str:
        """ Read all output from MPD until a message terminator is received

        Parameters
        ----------
        last_line_prefix : str | None
            If provided, stop reading data and return results when the received line begins with
            this prefix. Default ``None`` (read all data)

        Returns
        -------
        list[str] | str
            The cleansed lines returned from MPD or a `str` containing the error message on failure
        """
        retval = []
        err = "ACK "

        for line in self._read_line():
            if line in ("OK", "list_OK", None):
                break
            if last_line_prefix is not None and line.startswith(last_line_prefix):
                retval.append(line)
                break
            if line.startswith(err):
                return line.lstrip(err)
            retval.append(line)
        return retval

    def _init_connection(self, first_connect: bool = False) -> None:
        """ Initialize the connection confirm OK message and set the appropriate :attr:`ready`
        status

        Parameters
        -----------
        first_connect : bool
            ``True`` if this the first connection to MPD otherwise ``False``
        """
        ok = "OK MPD "
        msg = self.communicate("", last_line_prefix=ok)
        msg = msg[-1] if isinstance(msg, list) else msg
        if not msg.startswith(ok):
            message = f"MPD did not initialize correctly. Output: '{msg}'"
            logger.error(message)
            self._status = message
            return

        if first_connect:
            logger.info("Connected to MPD v%s", msg.lstrip(ok))
        self._status = "Ready"

        if self._password is None:
            return

        response = self.communicate(f"password {self._password}")
        if self._password is not None and isinstance(response, str):
            self._status = f"Error setting MPD password: {response}"

    def _disconnect(self) -> None:
        """ Disconnect from MPD """
        logger.debug("Disconnecting from MPD")
        self._sock.close()

    def _reconnect(self) -> str | None:
        """ Re-create the socket and file descriptors for a new connection to MPD

        Returns
        -------
        str | None
            ``None`` if reconnect was successful. Error message if unsuccessful
        """
        self._disconnect()
        logger.debug("Reconnecting to MPD")
        sock = self._connect()
        if isinstance(sock, str):
            return sock
        self._sock = sock
        self._init_connection()
        if self._status != "Ready":
            return self._status
        return None

    @T.overload
    def communicate(self,
                    command: str | list[str],
                    as_dict: T.Literal[True],
                    last_line_prefix: str | None = None) -> dict[str, str] | str: ...

    @T.overload
    def communicate(self,
                    command: str | list[str],
                    as_dict: T.Literal[False] = ...,
                    last_line_prefix: str | None = None) -> list[str] | str: ...

    def communicate(self,
                    command: str | list[str],
                    as_dict: bool = False,
                    last_line_prefix: str | None = None) -> dict[str, str] | list[str] | str:
        """ Send a line to MPD and return it's response

        Parameters
        ----------
        command : str | list[str]
            The command or list of commands to send to MPD
        as_dict : bool
            ``True`` to return the result splitting key, value pairs into a dictionary.
            ``False`` to return a list of each output line. Default ``False``
        last_line_prefix : str | None
            If provided, stop reading data and return results when the received line begins with
            this prefix. Default ``None`` (read all data)

        Returns
        -------
        dict[str, str] | list[str] | str
            The response from MPD. This will be:
            - A list of line strings if `as_dict` is ``False``.
            - A dictionary of {key: value} pairs if `as_dict` is ``True``.
            - A single `str` containing the error message if there was an error reading from MPD.
        """
        if isinstance(command, list):
            cmds = ["command_list_begin"] + command + ["command_list_end"]
        else:
            cmds = [command]

        attempt = 0
        while True:
            try:
                if command:  # Initial call to MPD or an 'idle' read will not contain a command
                    for cmd in cmds:
                        self._write_line(cmd)

                # idle returns are read in non-blocking in thread
                # noidle doesn't return if issued immediately after a read in idle
                if isinstance(command, str) and command.startswith("idle") or (
                        command == "noidle" and not self.data_available):
                    return []
                lines = self._read_lines(last_line_prefix=last_line_prefix)
                break
            except (ConnectionError, socket.timeout, OSError, ValueError) as err:
                if isinstance(err, ValueError) and "operation on closed file" not in str(err):
                    raise
                msg = f"{err.__class__.__name__}: {str(err)}"
                if attempt == self._retry_count:
                    logger.debug("%s. Giving up after %s retries", msg, self._retry_count)
                    self._disconnect()
                    return msg
                attempt += 1

                logger.debug("%s. Retrying %s of %s", msg, attempt, self._retry_count)
                err_msg = self._reconnect()
                if err_msg is not None:
                    return err_msg

        if as_dict and isinstance(lines, list):
            return self._list_to_dict(lines)
        return lines

    def _get_binary_info(self, command: str, offset: int) -> tuple[int, int, list[str]] | str:
        """ Obtain information about binary data to be extracted from the socket

        Parameters
        ----------
        command : str
            The command to send to return binary data information for
        offset : int
            The offset within the binary data to return the information for

        Returns
        -------
        size: int
            The total amount of data available for this command
        chunk: int
            The amount of binary data available from this query
        info: list[str]
            Any additional text information returned for this query.

        On error a single str containing the error message is returned
        """
        size = 0
        text = []
        lines = self.communicate(f"{command} {offset}", last_line_prefix=self._binary_prefix)
        if isinstance(lines, str):
            return lines

        binary_seen = False
        for line in lines:
            if line.startswith("size:"):
                size = int(line.split(":", maxsplit=1)[-1].strip())
            elif line.startswith(self._binary_prefix):
                chunk = int(line.split(":", maxsplit=1)[-1].strip())
                binary_seen = True
                break
            else:
                text.append(line)

        if not binary_seen:
            return "Binary key not found in MPD output"

        size = size if size else chunk
        logger.debug("Binary data size: %s, this chunk: %s, info: %s", size, chunk, text)
        return size, chunk, text

    def get_binary_data(self, command: str) -> tuple[bytes, list[str]] | str:
        """ Send a line to MPD and obtain the binary data output

        Parameters
        ----------
        command : str
            The command to send to MPD to obtain the required binary data

        Returns
        -------
        data : bytes
            The requested binary data
        info: list[str]
            Any additional text data received as part of the request

        On error a single str containing the error message is returned
        """
        offset = 0
        ret_bytes = b''
        ret_text = set()

        binary_info = self._get_binary_info(command, offset)
        if isinstance(binary_info, str):
            logger.debug("Error getting binary information: %s", binary_info)
            return binary_info

        remaining, chunk_size, text = binary_info
        ret_text.update(text)

        while True:
            chunk = self._get_bytes(chunk_size, is_exact=True)
            self.communicate("")  # Clear the OK output
            ret_bytes += chunk
            offset += chunk_size
            remaining -= chunk_size
            logger.debug("bytes read: %s bytes remaining: %s", len(ret_bytes), remaining)
            if len(chunk) == 0:
                msg = "No binary data available!"
                logger.error(msg)
                return msg
            if remaining == 0:
                logger.debug("All binary data collected")
                break

            binary_info = self._get_binary_info(command, offset)
            if isinstance(binary_info, str):
                logger.debug("Error getting binary information: %s", binary_info)
                return binary_info

            _, chunk_size, text = binary_info
            ret_text.update(text)

        retval = ret_bytes, list(ret_text)
        logger.debug("Got %s bytes of binary data. Associated text: %s ",
                     len(retval[0]), retval[1])
        return retval


ErrorType = dict[T.Literal["error"], dict[T.Literal["code", "message"], T.Union[int, str]]]
""" Error message formatted for returning via jsonrpc """
OKType = T.TypedDict("OKType", {"result": T.Literal["ok"]})
""" dict[Literal["result"], Literal["ok"]]: Ok message formatted for returning via jsonrpc """
ResultType = T.TypedDict("ResultType", {"result": dict[str, T.Any]})
""" dict[Literal["result], dict[str, Any]] Results message formatted for returning via jsonrpc """
NotifyType = T.TypedDict("NotifyType", {"method": str, "params": dict[str, T.Any]})
""" dict[Literal["method", "params"], str | dict[str, Any]] Notification message formatted for
returning via jsonrpc """


class Interface:
    """ Acts as the go-between SnapServer and the control source

    Parameters
    -----------
    source : :class:`MPD`
        The object responsible for returning data from the control source
    """
    def __init__(self, source: MPD) -> None:
        self._source = source
        self._last_properties: dict[str, T.Any] = {}
        self._last_metadata: dict[str, T.Any] = {}

    @classmethod
    @T.overload
    def _process_request_result(cls, result: str) -> ErrorType: ...

    @classmethod
    @T.overload
    def _process_request_result(cls, result: None) -> OKType: ...

    @classmethod
    def _process_request_result(cls, result: str | None) -> OKType | ErrorType:
        """ Process the result from an actioned RPC request

        Parameters
        ----------
        result : str | None
            ``None``if the request was performed successfully. A `str` containing error information
            on failure

        Returns
        -------
        dict[Literal["result", "error"], Any]
            The jsonrpc formatted "ok" result on success or "error" message if an error occurred
        """
        if result is None:
            return {"result": "ok"}
        return {"error": {"code": 9,
                          "message": f"Error during execution: {result}"}}

    @property
    def status(self) -> T.Literal["Ready"] | str:
        """ Literal["Ready"] | str : `"Ready"` if the source has been connected to and is ready to
        receive commands, otherwise a string indicating what the failure is """
        return self._source.status

    @property
    def update_available(self) -> bool:
        """ bool : ``True`` if the source has indicated that a properties/metadata update is
        available otherwise ``False`` """
        if not hasattr(self._source, "update_available"):
            return False
        return self._source.update_available

    def _validate_method(self, method: str) -> ErrorType | None:
        """ Validate the the source has the method that is being requested. If not, return an error

        Parameters
        ----------
        method : str
            The method name that is to be validated

        Returns
        -------
        dict[Literal["error"], Any] | None
            ``None`` if the requested method exists, otherwise a jsonrpc formatted error
        """
        if not hasattr(self._source, method):
            err = (f"Method not found: Source '{self._source.__class__.__name__}' does not "
                   f"implement method '{method}'")
            return {"error": {"code": -32601, "message": err}}
        return None

    def _get_properties_and_metadata(self) -> tuple[SnapProperties, SnapMetadata] | ErrorType:
        """ Obtain the latest properties and metadata from MPD

        Returns
        -------
        properties : :class:`SnapProperties`
            The SnapCast properties object
        metadata : :class:`SnapMetadata`
            The SnapCast metadata object

        On error a `Dict[str, Any]` rpc formatted error message is returned
        """
        for method in ("get_properties", "get_metadata"):
            not_exist = self._validate_method(method)
            if not_exist:
                return not_exist

        properties = self._source.get_properties()
        if isinstance(properties, str):
            return self._process_request_result(properties)

        metadata = self._source.get_metadata()
        # TODO reset SnapMeta here if possible, to take it out of plugin requirement
        if isinstance(metadata, str):
            return self._process_request_result(metadata)

        return properties, metadata

    @T.overload
    def get_properties(self, is_requested: T.Literal[True] = ...
                       ) -> ResultType | NotifyType | ErrorType: ...

    @T.overload
    def get_properties(self, is_requested: T.Literal[False]
                       ) -> ResultType | NotifyType | ErrorType | None: ...

    def get_properties(self,
                       is_requested: bool = True) -> ResultType | NotifyType | ErrorType | None:
        """ Obtain the current :class:`SnapProperties` and :class:`SnapMetadata` from the control
        source and return ready for serializing to jsonrpc. Store the metadata so it can be checked
        for future calls to :func:`get_properties`

        Parameters
        ----------
        is_requested : bool
            ``True`` if responding to a getProperties request from SnapServer. ``False`` if sending
            an unrequested notification. Default is ``True``

        Returns
        -------
        dict[str, Any] | None
            The rpc formatted current properties received from the control source or an rpc
            formatted error message on failure.

            If `is_requested` is ``True`` this will always return the full properties, including
            MetaData, formatted as an rpc request response.

            If `is_requested` is ``False`` then properties will only be returned if there has been
            an update since the last call, otherwise ``None`` will be returned. MetaData will only
            be included if it has changed since the last response. The response will be formatted
            as an rpc notification.
         """
        data = self._get_properties_and_metadata()
        if isinstance(data, dict):  # This is an error message
            return data

        properties, metadata = data
        properties_rpc = properties.rpc_data
        metadata_rpc = metadata.rpc_data

        if is_requested:
            logger.debug("get_properties: %s, %s", properties, metadata)
            self._last_properties = properties_rpc.copy()
            self._last_metadata = metadata_rpc
            properties_rpc["metadata"] = metadata_rpc
            return {"result": properties_rpc}

        if properties_rpc == self._last_properties and metadata_rpc == self._last_metadata:
            logger.debug("Properties are unchanged. Not returning")
            return None

        self._last_properties = properties_rpc.copy()

        if metadata_rpc == self._last_metadata:
            logger.debug("properties: %s", properties)
        else:
            self._last_metadata = metadata_rpc
            logger.debug("properties: %s, %s", properties, metadata)
            properties_rpc["metadata"] = metadata_rpc

        return {"method": "Plugin.Stream.Player.Properties",
                "params": properties_rpc}

    def play(self) -> OKType | ErrorType:
        """ Start playback

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("play")
        if not_exist:
            return not_exist
        return self._process_request_result(self._source.play())  # type:ignore[attr-defined]

    def pause(self) -> OKType | ErrorType:
        """ Pause playback

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("pause")
        if not_exist:
            return not_exist
        return self._process_request_result(self._source.pause())  # type:ignore[attr-defined]

    def play_pause(self) -> OKType | ErrorType:
        """ Toggle play/pause

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("play_pause")
        if not_exist:
            return not_exist
        return self._process_request_result(self._source.play_pause())  # type:ignore[attr-defined]

    def stop(self) -> OKType | ErrorType:
        """ Stop playback

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("stop")
        if not_exist:
            return not_exist
        return self._process_request_result(self._source.stop())  # type:ignore[attr-defined]

    def next(self) -> OKType | ErrorType:
        """ Skip to the next track

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("next")
        if not_exist:
            return not_exist
        return self._process_request_result(self._source.next())  # type:ignore[attr-defined]

    def previous(self) -> OKType | ErrorType:
        """ Skip to the previous track

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("previous")
        if not_exist:
            return not_exist
        return self._process_request_result(self._source.previous())  # type:ignore[attr-defined]

    def seek(self, offset: float) -> OKType | ErrorType:
        """ Seek forwards or backwards from the current position

        Parameters
        ----------
        offset : float
            The seek offset, in seconds, from the current position

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("seek")
        if not_exist:
            return not_exist
        return self._process_request_result(
            self._source.seek(float(offset)))  # type:ignore[attr-defined]

    def set_position(self, position: float) -> OKType | ErrorType:
        """ Set the absolute track position

        Parameters
        ----------
        position : float The position, in seconds, to set for the track

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("set_position")
        if not_exist:
            return not_exist
        return self._process_request_result(
            self._source.set_position(float(position)))  # type:ignore[attr-defined]

    def set_loop_status(self, status: T.Literal["none", "track", "playlist"]
                        ) -> OKType | ErrorType:
        """ Set the current loop status

        Parameters
        ----------
        status : Literal["none", "track", "playlist"]
            `"none"` to disable looping, `"track"` to loop the current track, `"playlist"` to loop
            the entire playlist

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("set_loop_status")
        if not_exist:
            return not_exist

        status = T.cast(T.Literal["none", "track", "playlist"], status.lower())
        valid = ("none", "track", "playlist")
        if status not in valid:
            return {"error": {"code": -32602,
                              "message": f"Invalid params: loopStatus should be one of {valid}"}}
        return self._process_request_result(
            self._source.set_loop_status(status))  # type:ignore[attr-defined]

    def set_shuffle(self, shuffle: bool) -> OKType | ErrorType:
        """ Enable or disable playlist shuffle status

        Parameters
        ----------
        shuffle : bool
            ``True`` to enable shuffle. ``False`` to disable

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("set_shuffle")
        if not_exist:
            return not_exist
        return self._process_request_result(
            self._source.set_shuffle(shuffle))  # type:ignore[attr-defined]

    def set_volume(self, volume: int) -> OKType | ErrorType:
        """ Set the volume

        Parameters
        ----------
        volume : int
            The percent to set the volume to

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("set_volume")
        if not_exist:
            return not_exist

        return self._process_request_result(
            self._source.set_volume(int(volume)))  # type:ignore[attr-defined]

    def set_mute(self, mute: bool) -> OKType | ErrorType:
        """ Enable or disable mute

        Parameters
        ----------
        mute : bool
            ``True`` to mute. ``False`` to disable mute

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("set_mute")
        if not_exist:
            return not_exist
        return self._process_request_result(
            self._source.set_mute(mute))  # type:ignore[attr-defined]

    def set_rate(self, rate: float) -> OKType | ErrorType:
        """ Set the playback rate

        Parameters
        ----------
        rate : float
            The playback rate to set

        Returns
        -------
        dict[str, Any]
            The rpc formatted "ok" result on success or "error" message if an error occurred
        """
        not_exist = self._validate_method("set_rate")
        if not_exist:
            return not_exist
        return self._process_request_result(
            self._source.set_rate(float(rate)))  # type:ignore[attr-defined]


class MonitorStdIn:
    """ Monitors stdin in a background thread for inbound commands from SnapServer """
    def __init__(self) -> None:
        self._queue: Queue = Queue()
        self._thread = Thread(target=self._monitor,
                              name="stdin_mon",
                              args=(self._queue, ),
                              daemon=True)

    @staticmethod
    def _monitor(queue: Queue) -> None:
        """ Monitor stdin for jsonrpc 2.0 messages from inside a thread and put to queue

        Parameters
        ----------
        queue : class:`queue.Queue`
            The queue to pass received messages to
        """
        for line in sys.stdin:
            logger.debug("Message received: '%s'", line.strip())
            if 'jsonrpc' not in line and '2.0' not in line:
                logger.debug("Ignoring non-jsonrpc message")
                continue
            queue.put(line)

    def monitor(self) -> T.Generator[str | None, None, None]:
        """ Obtain any jsonrpc messages received from stdin

        Yields
        ------
        str | None
            A message received from stdin or ``None`` if there are no messages available
        """
        while True:
            try:
                line = self._queue.get(timeout=0.2)
            except QueueEmpty:
                line = None
            yield line

    def start(self) -> None:
        """ Start the stdin monitor """
        logger.debug("Starting stdin monitor")
        self._thread.start()


class SnapServer:  # pylint: disable=too-many-instance-attributes
    """ Handles sending and receiving jsonrpc messages to and from SnapServer

    Parameters
    ----------
    interface : :class:`Interface`
        The object responsible for communicating with the control source
    requery_time : int
        The time, in seconds, to requery the source for updated properties
    """
    def __init__(self, interface: Interface, requery_time: int) -> None:
        self._base_message = {"jsonrpc": "2.0"}
        self._requery = requery_time
        self._last_properties_call = -1.0

        self._properties_request = "Plugin.Stream.Player.GetProperties"
        self._methods: dict[str,
                            T.Callable[[dict[str, T.Any]], None] |
                            T.Callable[[bool],
                                       ResultType | NotifyType | ErrorType | None]] = {
            "Plugin.Stream.Player.Control": self.control,
            "Plugin.Stream.Player.SetProperty": self.set_property,
            self._properties_request: interface.get_properties}
        """ dict[str, Callable] : Mapping of jsonrpc commands to their method. Methods that call an
        interface method directly take no arguments. Methods that are bound to this class take the
        received deserialized json as their sole argument """

        self._controls: dict[str, T.Callable[..., OKType | ErrorType]] = {
            "play": interface.play,
            "pause": interface.pause,
            "playPause": interface.play_pause,
            "stop": interface.stop,
            "next": interface.next,
            "previous": interface.previous,
            "seek": interface.seek,
            "setPosition": interface.set_position}
        """ dict[str, Callable] : Mapping of jsonrpc control commands to their method. Provided
        method parameters (if any) should be the same as their corresponding method's arguments """

        self._properties: dict[str, T.Callable[..., OKType | ErrorType]] = {
            "loopStatus": interface.set_loop_status,
            "shuffle": interface.set_shuffle,
            "volume": interface.set_volume,
            "mute": interface.set_mute,
            "rate": interface.set_rate}
        """ dict[str, Callable] : Mapping of jsonrpc "Set Property" commands to their method """

        self._interface = interface
        self._stdin = MonitorStdIn()

        self.ready()
        self.main_loop()

    def _shrink_for_logging(self, message: dict[str, T.Any]) -> dict[str, T.Any]:
        """ Utility function to change long entries to a representation showing the length for
        logging purposes

        Parameters
        ----------
        message : dict[str, Any]
            The dictionary that is to be json serialized

        Returns
        -------
        dict[str, Any]
            A copy of the original dictionary with any text entries longer than 100 characters
            replaced with metadata indicating the length
        """
        retval = {}
        for key, val in message.copy().items():
            if isinstance(val, str) and len(val) > 100:
                val = f"<length: {len(val)}>"
            if isinstance(val, dict):
                val = self._shrink_for_logging(val)
            retval[key] = val
        return retval

    def send(self, message: dict[str, T.Any], rpc_id: int | None = None) -> None:
        """ Send the jsonrpc message to STDOUT

        Parameters
        -----------
        message : dict[str, Any]
            The message to output to stdout for picking up by SnapServer
        rpc_id : int
            The rpc id for from SnapServer request. Default; ``None`` (no id)
        """
        message = self._base_message | message
        if rpc_id is not None:
            message = message | {"id": rpc_id}
        logger.debug("Sending json: %s", json.dumps(self._shrink_for_logging(message)))
        print(json.dumps(message), file=sys.stdout)
        sys.stdout.flush()

    def log(self,
            message: str,
            level: T.Literal["trace", "debug", "info", "notice", "warning", "error", "fatal"]
            ) -> None:
        """ Send a log message upstream to SnapServer

        Parameters
        ----------
        message : str
            The log message to send to SnapServer
        level : Literal["trace", "debug", "info", "notice", "warning", "error", "fatal"]
            The level to log at
        """
        self.send({"method": "Plugin.Stream.Log",
                   "params": {"severity": level, "message": message}})

    def ready(self) -> None:
        """ Send a message to SnapServer that we are ready or send a log error and exit on
        failure """
        if self._interface.status == "Ready":
            self.send({"method": "Plugin.Stream.Ready"})
        else:
            logger.error(self._interface.status)
            self.log(self._interface.status, "error")
            sys.exit(1)

    def _get_params(self, message: dict[str, T.Any]) -> dict[str, T.Any] | None:
        """ Obtain the parameters from the incoming message. If they do not exist, send an error
        message to SnapServer

        Parameters
        ----------
        message : dict[str, Any]
            The incoming deserialized jsonrpc message

        Returns
        -------
        dict[str, Any]
            The parameters from the incoming message or ``None`` if they do not exist
        """
        retval = message.get("params")
        if not retval:
            err = f"No params supplied for '{message['method']}'"
            self.send({"error": {"code": -32602, "message": err}}, message.get("id"))
            retval = None
        return retval

    def control(self, message: dict[str, T.Any]) -> None:
        """ Process an inbound Control command and send success or failure to SnapServer

        Parameters
        ----------
        message : dict[str, Any]
            The inbound RPC message from SnapServer
        """
        if self._last_properties_call < 0:
            msg = f"Invalid Request: {self._properties_request} has not been requested"
            self.send({"error": {"code": -32600,
                                 "message": msg}}, message.get("id"))
            return

        command_details = self._get_params(message)
        if command_details is None:
            return

        control = command_details.get('command')
        if control not in self._controls:
            err = f"Invalid command for method '{message['method']}': '{control}'"
            self.send({"error": {"code": -32602, "message": err}}, message.get("id"))
            return

        method = self._controls[control]
        f_args = [a for a in method.__code__.co_varnames[:method.__code__.co_argcount]
                  if a != "self"]
        params: dict[str, T.Any] = command_details.get("params", {})
        if not set(f_args).issubset(set(params)):
            err = (f"Invalid params. Method '{message['method']} - command '{control}' "
                   f"requires: {f_args}. Received: {list(params)}")
            self.send({"error": {"code": -32602, "message": err}}, message.get("id"))
            return

        kwargs = {a: params.pop(a) for a in f_args}
        if params:
            logger.debug("Ignoring additional unused params for control '%s': %s",
                         control, params)

        self.send(method(**kwargs), message.get("id"))

    def set_property(self, message: dict[str, T.Any]) -> None:
        """ Process an inbound SetProperty command

        Parameters
        ----------
        message : dict[str, Any]
            The inbound RPC message from SnapServer
        """
        if self._last_properties_call < 0:
            msg = f"Invalid Request: {self._properties_request} has not been requested"
            self.send({"error": {"code": -32600,
                                 "message": msg}}, message.get("id"))
            return

        params = self._get_params(message)
        if params is None:
            return

        if len(params) > 1:
            err = (f"Invalid params. Only one param should be provided for method "
                   f"'{message['method']}', got {list(params)}")
            self.send({"error": {"code": -32602, "message": err}}, message.get("id"))
            return

        prop, value = next((k, v) for k, v in params.items())
        method = self._properties.get(prop)
        if method is None:
            err = f"Invalid params. Property '{prop}' is not valid for method '{message['method']}"
            self.send({"error": {"code": -32602, "message": err}}, message.get("id"))
            return

        self.send(method(value), message.get("id"))

    def notify_properties(self,
                          reason: T.Literal["schedule", "source", "server"] = "scheduled") -> None:
        """ Return the current Properties to SnapServer either on a control call or scheduled by
        query interval

        Parameters
        ----------
        force_update : bool
            ``True`` to update immediately, regardless of time since last query.
            ``False`` to only update if enough time has elapsed since the last query.
            Default: ``False``
        """
        if self._last_properties_call < 0:  # Initial call from SnapServer not yet processed
            return

        if reason == "schedule" and (time() - self._last_properties_call < self._requery):
            return

        log_reason = {"schedule": "scheduled",
                      "source": "update from source",
                      "server": "request from server"}
        logger.debug("Updating properties for reason: %s", log_reason[reason])
        properties = self._interface.get_properties(is_requested=False)
        if properties is not None:
            self.send(properties)
        self._last_properties_call = time()

    def _from_json(self, line: str) -> dict[str, T.Any] | None:
        """ Parse the received jsonrpc message to a Python dictionary. Send an error back if the
        json is malformed

        Parameters
        ----------
        line : str
            The line received from STDIN

        Returns
        -------
        dict[str, Any]
            The parsed json or ``None`` if there was a decoding error
        """
        try:
            retval: dict[str, T.Any] | None = json.loads(line)
        except json.JSONDecodeError as err:
            self.send({"error": {"code": -32700,
                       "message": f"Invalid JSON was received by the server: {str(err)}"}})
            retval = None
        return retval

    def _process_request(self, message: dict[str, T.Any]) -> None:
        """ Process an incoming request from SnapServer

        Parameters
        ----------
        message : dict[str, Any]
            A validated incoming message from SnapServer that contains the 'method' key
        """
        method = self._methods[message["method"]]
        logger.debug("Processing %s message: %s", method.__name__, message)
        if method.__self__ == self:  # method is a member of this class
            method(message)
            self.notify_properties(reason="server")
        else:
            # method is properties retrieval handled by Interface
            self.send(method(), message.get("id"))
            if self._last_properties_call < 0 and message["method"] == self._properties_request:
                self._last_properties_call = time()
                logger.debug("Setting initial properties call time: %s",
                             self._last_properties_call)

    def main_loop(self) -> None:
        """ Monitor STDIN for messages from SnapServer and handle accordingly """
        self._stdin.start()
        for line in self._stdin.monitor():
            if line is None and self._interface.update_available:
                self.notify_properties(reason="source")
                continue

            if line is None:
                self.notify_properties(reason="schedule")
                continue

            message = self._from_json(line)
            if message is None:
                logger.debug("Not processing invalid json")
                continue

            if message.get("method") not in self._methods:
                logger.debug("Invalid method: %s", message.get("method"))
                err = {"error": {"code": -32601,
                                 "message": f"The method does not exist: {message.get('method')}"}}
                self.send(err, message.get("id"))
                continue

            self._process_request(message)


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        raise ValueError("This program requires at least Python 3.9")

    args = CliArgs()
    logger = _configure_logging(args.debug, args.log_dir)
    logger.debug("cliArgs: %s", args)
    snap_interface = Interface(MPD(args.mpd_host, args.mpd_port, args.mpd_password))
    snap_server = SnapServer(snap_interface, args.mpd_requery)
