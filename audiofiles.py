import hashlib
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import mutagen
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from iOpenPod.app.media.lyrics import embedded_lyrics
from iPodDB.library import AudioEncoding, Track, TrackMetadata

EXTENSIONS = {'.mp3', '.m4a', '.aac', '.flac', '.wav', '.aif', '.aiff', '.ogg', '.opus', '.wma', '.alac'}

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def encoded_audio_digest(path):
    """Compare a previously imported file after an embedded-lyrics edit."""
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-xerror',
               '-i', str(path), '-map', '0:a:0', '-c:a', 'copy', '-f', 'hash', '-hash', 'sha256', '-']
    result = subprocess.run(command, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    value = result.stdout.strip()
    if result.returncode == 0 and re.fullmatch(rb'SHA256=[a-fA-F0-9]{64}',value):
        return value
    return None

def number(value):
    match = re.match(r'\d{1,5}', str(value))
    return min(int(match[0]), 65535) if match else 0

def read_tags(path):
    try:
        easy = mutagen.File(path, easy=True)
    except Exception:
        easy = None
    tags = easy.tags if easy is not None and easy.tags else {}
    def field(name, *alternatives):
        for key in (name, *alternatives):
            data = tags.get(key)
            if data:
                if hasattr(data, 'text'):
                    data = data.text
                if isinstance(data, (list, tuple)):
                    data = data[0]
                return str(data).strip().replace('\0', '')[:1024]
        return ''
    return dict(title=field('title', 'TIT2', 'Title'),
                artist=field('artist','TPE1','Author'), album=field('album','TALB','WM/AlbumTitle'),
                album_artist=field('albumartist','TPE2','WM/AlbumArtist'), genre=field('genre','TCON','WM/Genre'),
                track_number=number(field('tracknumber','TRCK','WM/TrackNumber')), year=number(field('date','TDRC','WM/Year')))

@dataclass
class AudioFile:
    path: Path
    checksum: str
    track: Track
    encoding: AudioEncoding
    extension: str

def prepare_audio(path, temp, index):
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError('不支持这个扩展名。')
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('文件为空或无法访问。')
    tags = read_tags(path)
    try:
        parsed = mutagen.File(path)
    except Exception:
        parsed = None
    info = getattr(parsed, 'info', None)
    compatible = (info and 0 < getattr(info, 'sample_rate', 0) <= 48000 and
                  getattr(info, 'channels', 0) in (1, 2))
    path_out = path
    if isinstance(parsed, MP3) and compatible and info.layer == 3 and 0 < info.bitrate <= 320500:
        encoding, extension, description = AudioEncoding.MP3, '.mp3', 'MPEG audio file'
    elif isinstance(parsed, MP4) and compatible and (
        (info.codec == 'mp4a.40.2' and 0 < info.bitrate <= 320500) or
        (info.codec == 'alac' and getattr(info, 'bits_per_sample', 0) in (16, 0))):
        is_alac = info.codec == 'alac'
        encoding = AudioEncoding.ALAC if is_alac else AudioEncoding.AAC
        extension, description = '.m4a', 'Apple Lossless audio file' if is_alac else 'AAC audio file'
    else:
        path_out = temp / f'audio-{index}.m4a'
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error',
                   '-xerror', '-i', str(path), '-map', '0:a:0', '-vn', '-map_metadata', '0',
                   '-c:a', 'aac', '-profile:a', 'aac_low', '-ar', '44100', '-ac', '2',
                   '-b:a', '192k', '-movflags', '+faststart', str(path_out)]
        proc = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        if proc.returncode:
            raise ValueError('音频解码失败。请确认文件可以本地播放，且没有 DRM 加密。')
        parsed = MP4(path_out)
        info = parsed.info
        converted_tags = read_tags(path_out)
        tags = {name: value or converted_tags[name] for name, value in tags.items()}
        encoding, extension, description = AudioEncoding.AAC, '.m4a', 'AAC audio file'
    if not info or info.length <= 0:
        raise ValueError('无法读取音轨时长。')
    length = path_out.stat().st_size
    if length >= 2**32:
        raise ValueError('单个文件超过 iPod 文件系统的大小限制。')
    tags['title'] = tags['title'] or path.stem
    track = Track(-index-1, **tags, length_ms=max(1, round(info.length*1000)),
                  size_bytes=length, bitrate_kbps=round((info.bitrate or length*8/info.length)/1000),
                   metadata=TrackMetadata(file_format=description, lyrics=embedded_lyrics(parsed), sample_rate_hz=info.sample_rate,
                                         date_added=int(time.time())))
    return AudioFile(path_out, digest(path_out), track, encoding, extension)
