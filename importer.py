"""Append verified media to a nano 3G database. All publishing goes through one transaction."""
import contextlib
import ctypes
import hashlib
import json
import msvcrt
import os
import re
import shutil
import tempfile
import uuid
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from iPodDB.library import (FileDependency, IPodLibrary, PreparedLyrics, WriteChecksum, WriteResources,
                            WriteTarget, create_empty_library, is_playback_sidecar, prepared_audio)
from iPodDB.iTunesDB.writer.signature import verify_hash58
from iPodDB.sidecars import MAX_SIDECAR_BYTES, remap_playback_sidecar
from audiofiles import digest, encoded_audio_digest, prepare_audio

APP_DATA = Path(os.environ['LOCALAPPDATA']) / 'NanoDrop'
BACKUPS = APP_DATA / '曲库备份'

def target(device):
    return WriteTarget(checksum=WriteChecksum.HASH58, firewire_guid=device.guid, database_version=0x30)

def db_bytes(device):
    device.check()
    db = device.db
    if (db.parent / 'iTunesCDB').is_file():
        raise ValueError('检测到压缩曲库。此版本支持第三代 nano 的标准 iTunesDB。')
    if db.exists():
        if not 0 < db.stat().st_size <= 256 * 1024**2:
            raise ValueError('原曲库大小异常。请先保留设备内容并检查连接。')
        return db.read_bytes()
    return None

def source_library(device, raw):
    if raw is not None:
        lib = IPodLibrary(raw)
        if any(raw[0x58:0x6c]) and not verify_hash58(raw, device.guid):
            raise ValueError('原曲库的校验与这台 iPod 不匹配，已停止写入。')
        return lib
    media = device.root / 'iPod_Control/Music'
    if (media.exists() and any(p.is_file() for p in media.rglob('*'))) or (
            device.db.parent.exists() and any(p.name.casefold().startswith('itunesdb') for p in device.db.parent.iterdir())):
        raise ValueError('设备上留有媒体或旧曲库，但找不到主曲库。请先恢复原曲库。')
    created = create_empty_library(device.label or 'iPod nano', target(device), supports_podcasts=True)
    if not created.prepared:
        raise ValueError('无法建立适用于第三代 nano 的曲库。')
    return IPodLibrary(created.prepared.itunes)

def summary(device):
    raw = db_bytes(device)
    library = source_library(device, raw)
    device.check()
    return (library.snapshot.device_name or device.label, library.snapshot.tracks, shutil.disk_usage(device.root).free)

def original_hash(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None

def write_file(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())

def windows_path(path):
    full = os.path.abspath(path)
    if full.startswith('\\\\?\\'):
        return full
    if full.startswith('\\\\'):
        return '\\\\?\\UNC\\' + full[2:]
    return '\\\\?\\' + full

def backup_folder(device, root=None):
    return Path(windows_path(root or BACKUPS)) / (device.guid.hex() + '-' + format(device.volume, '08x'))

def move_file(src, dst, overwrite=False):
    flags = 0x8 | int(overwrite)  # MOVEFILE_WRITE_THROUGH and optional REPLACE_EXISTING
    if not ctypes.windll.kernel32.MoveFileExW(windows_path(src), windows_path(dst), flags):
        raise ctypes.WinError()

@contextlib.contextmanager
def writer_lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'writer.lock').open('a+b') as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise OSError('已有另一个窗口正在向这台 iPod 导入歌曲。') from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

def safe_media(root, relative):
    p = (root / relative).resolve()
    if not p.is_relative_to((root / 'iPod_Control/Music').resolve()):
        raise ValueError('曲库中的媒体位置不在音乐目录内。')
    return p

def assign_paths(device, library, audio, progress):
    existing = defaultdict(list)
    indexed_paths = defaultdict(list)
    for track in library.snapshot.tracks:
        if track.metadata.location:
            existing[track.size_bytes].append(track.metadata.location)
            indexed_paths[track.metadata.location.casefold()].append(track)
    hashes, used, additions, repairs = {}, set(), [], []
    reserved = set()
    for item in audio:
        progress('检查重复曲目 · ' + item.track.title)
        if item.checksum in used:
            continue
        used.add(item.checksum)
        for loc in existing[item.track.size_bytes]:
            if loc not in hashes:
                candidate = safe_media(device.root, loc)
                hashes[loc] = digest(candidate) if candidate.is_file() else ''
            if hashes[loc] == item.checksum:
                break
        else:
            folder = int(item.checksum[:2], 16) % 20
            duplicate = False
            for start in range(0, 52, 4):
                rel = f'iPod_Control/Music/F{folder:02d}/ND{item.checksum[start:start+12]}{item.extension}'
                if rel.casefold() in reserved:
                    continue
                path = safe_media(device.root, rel)
                owners = indexed_paths.get(rel.casefold(), ())
                if owners and not path.exists():
                    if any(t.size_bytes != item.track.size_bytes for t in owners):
                        raise ValueError('缺失文件的大小与现有曲库记录不同，请从曲库备份恢复该文件。')
                    rel = owners[0].metadata.location
                    break
                if owners and path.is_file():
                    # A lyrics edit changes the container while preserving the recording.
                    if digest(path) == item.checksum:
                        duplicate = True
                        break
                    original_audio = encoded_audio_digest(item.path)
                    if original_audio and original_audio == encoded_audio_digest(path):
                        duplicate = True
                        break
                if not path.exists() or digest(path) == item.checksum:
                    break
            else:
                raise ValueError('无法分配可用的音乐文件名。')
            if duplicate:
                continue
            item.track = replace(item.track, metadata=replace(item.track.metadata, location=rel))
            reserved.add(rel.casefold())
            (repairs if owners else additions).append(item)
    return additions, repairs

def playback_files(device):
    device.check()
    folder = device.db.parent
    files = {}
    if folder.exists():
        for path in folder.iterdir():
            if path.is_file() and is_playback_sidecar(path.name):
                if path.stat().st_size > MAX_SIDECAR_BYTES:
                    raise ValueError('设备上的播放记录过大，无法验证。')
                files[path.name] = path.read_bytes()
    return files

def prepare_database(device, lib, items, history):
    tracks = lib.snapshot.tracks + tuple(item.track for item in items)
    plan = lib.analyze(lib.begin_draft(replace(lib.snapshot, tracks=tracks)), target(device))
    media = tuple(prepared_audio(i.track.track_id, FileDependency(i.track.metadata.location, i.track.size_bytes,
                                                                  i.checksum), i.encoding) for i in items)
    # All original positions are preserved. Known positional formats must remap
    # byte-for-byte, and the publisher rechecks the captured files before commit.
    old_ids = tuple(t.track_id for t in lib.snapshot.tracks)
    draft_ids = tuple(t.track_id for t in tracks)
    for name, data in history.items():
        if data[:4] in (b'mhdp', b'pdhm', b'mhpo', b'ophm'):
            if remap_playback_sidecar(data, old_ids, draft_ids) != data:
                raise ValueError(f'无法保留原播放记录：{name}')
    lyrics = tuple(PreparedLyrics(item.track.track_id, item.track.metadata.lyrics, asset.file)
                   for item, asset in zip(items, media) if item.track.metadata.lyrics)
    result = lib.prepare(plan, WriteResources(media=media, lyrics=lyrics, pending_playback_sidecars=False))
    if not result.prepared:
        reasons = [i.message for i in result.issues if i.severity.value == 'error'] or [i.message for i in result.issues]
        raise ValueError('曲库未通过写入前检查。\n' + '\n'.join(dict.fromkeys(reasons))[:1200])
    output = result.prepared.itunes
    after = IPodLibrary(output).snapshot
    before_ids = [(t.track_id, t.metadata.location) for t in lib.snapshot.tracks]
    if (not verify_hash58(output, device.guid) or len(after.tracks) != len(tracks) or
        before_ids != [(t.track_id, t.metadata.location) for t in after.tracks[:len(before_ids)]] or
        {i.track.metadata.location for i in items} != {t.metadata.location for t in after.tracks[len(before_ids):]}):
        raise ValueError('候选曲库的签名、原曲目顺序或新增曲目未通过复核。')
    return output,after.tracks[len(before_ids):]

def recover(device, journal):
    if not journal.exists():
        return
    device.check()
    info = json.loads(journal.read_text(encoding='utf-8'))
    current = original_hash(db_bytes(device))
    if current not in (info['before'], info['after']):
        raise ValueError('上次中断后曲库已被其他程序修改。请保留曲库备份后检查设备。')
    tx = info['tx']
    if not re.fullmatch('[a-f0-9]{32}', tx):
        raise ValueError('本机恢复记录异常。')
    for rel, sha in info['new']:
        if not re.fullmatch(r'iPod_Control/Music/F\d{2}/ND[a-f0-9]{12}\.(mp3|m4a)', rel):
            raise ValueError('本机恢复记录中的路径异常。')
        p = safe_media(device.root, rel)
        device.check()
        p.with_name(p.name + '.' + tx + '.part').unlink(missing_ok=True)
        if current == info['before'] and p.is_file() and digest(p) == sha:
            p.unlink()
    device.check()
    (device.db.parent / ('ND-' + tx + '.tmp')).unlink(missing_ok=True)
    journal.unlink()

def publish(device, raw, output, items, folder, history, progress):
    new = [i for i in items if not safe_media(device.root, i.track.metadata.location).exists()]
    required = sum(i.track.size_bytes for i in new) + len(output) + 1024**2
    if shutil.disk_usage(device.root).free < required:
        raise OSError('iPod 剩余容量不足。请减少本次导入的歌曲。')
    if raw is not None:
        backup = folder / ('iTunesDB-' + original_hash(raw) + '.bak')
        if not backup.exists():
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as fp:
                temp = Path(fp.name)
            try:
                with temp.open('wb') as fp:
                    fp.write(raw)
                    fp.flush()
                    os.fsync(fp.fileno())
                move_file(temp, backup)
            finally:
                temp.unlink(missing_ok=True)
        if digest(backup) != original_hash(raw):
            raise OSError('电脑上的原曲库备份未通过校验。')
    if db_bytes(device) != raw:
        raise OSError('准备期间设备曲库已变化，请重试。')
    tx = uuid.uuid4().hex
    journal = folder / 'pending.json'
    staged_journal = folder / (tx + '.json')
    write_file(staged_journal, json.dumps(dict(before=original_hash(raw), after=original_hash(output), tx=tx,
               new=[(i.track.metadata.location,i.checksum) for i in new])).encode())
    move_file(staged_journal, journal)
    try:
        device.db.parent.mkdir(parents=True, exist_ok=True)
        for idx, item in enumerate(items, 1):
            device.check()
            progress(f'写入并校验 {idx}/{len(items)} · {item.track.title}')
            dst = safe_media(device.root, item.track.metadata.location)
            if item in new:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(dst.name + '.' + tx + '.part')
                with item.path.open('rb') as src, tmp.open('xb') as out:
                    shutil.copyfileobj(src, out, 1024**2)
                    out.flush()
                    os.fsync(out.fileno())
                if digest(tmp) != item.checksum:
                    raise OSError('音乐文件写入校验失败，请检查数据线后重试。')
                device.check()
                move_file(tmp, dst)
            elif digest(dst) != item.checksum:
                raise OSError('设备上的音频文件发生变化，请重试。')
        progress('更新 iPod 音乐菜单…')
        temp_db = device.db.parent / ('ND-' + tx + '.tmp')
        write_file(temp_db, output)
        if temp_db.read_bytes() != output or db_bytes(device) != raw or playback_files(device) != history:
            raise OSError('曲库发生变化或写入不完整，已停止提交。')
        device.check()
        move_file(temp_db, device.db, overwrite=raw is not None)
        if db_bytes(device) != output:
            raise OSError('曲库写入后未能回读，请重新连接并检查设备。')
    finally:
        recover(device, journal)

def import_files(device, paths, progress=lambda message: None, backup_root=None, *, imported_tracks=None, restored_paths=None):
    from edit_tx import commit, resume_edit
    folder = backup_folder(device, backup_root)
    with writer_lock(folder), tempfile.TemporaryDirectory(prefix='NanoDrop-') as scratch:
        journal = folder / 'pending.json'
        resume_edit(device,folder)
        recover(device, journal)
        raw = db_bytes(device)
        lib = source_library(device, raw)
        audio = []
        for idx, path in enumerate(paths):
            device.check()
            progress(f'准备音频 {idx+1}/{len(paths)} · {path.name}')
            try:
                audio.append(prepare_audio(path, Path(scratch), idx))
            except Exception as exc:
                raise ValueError(f'{path.name}\n{exc}') from exc
        items, repairs = assign_paths(device, lib, audio, progress)
        if items:
            progress('生成并验证第三代 nano 曲库…')
            history = playback_files(device)
            output,added_tracks = prepare_database(device, lib, items, history)
            publish(device, raw, output, items + repairs, folder, history, progress)
            if imported_tracks is not None:
                imported_tracks.extend(added_tracks)
        elif repairs:
            def check_library():
                if db_bytes(device) != raw or any(digest(i.path) != i.checksum for i in repairs):
                    raise OSError('准备恢复时曲库或本地音频已变化，请重试。')
            commit(device, folder, {i.track.metadata.location:(None, i.path) for i in repairs},
                   check_library, progress)
        if restored_paths is not None:
            restored_paths.extend(i.track.metadata.location for i in repairs)
        written = len(items) + len(repairs)
        return written, len(paths) - written
