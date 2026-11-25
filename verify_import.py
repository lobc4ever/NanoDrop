"""Exercise the public importer only on a temporary directory on the PC."""
import argparse
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from iPodDB.library import IPodLibrary, WriteChecksum, WriteTarget, create_empty_library
from iPodDB.iTunesDB.writer.signature import verify_hash58
from iPodDB.iTunesDB.parser.parse_iTunesDB import parse_iTunesDB
from iPodDB.iTunesDB.shared.chunk_defs.mhyp import MhypHeader
from iPodDB.iTunesDB.shared.chunk_defs.mhip import MhipHeader
import importer
from device import DB_REL, discover

@dataclass
class Replica:
    root: Path
    guid: bytes
    volume: int = 1
    label: str = 'Nano QA'
    online: bool = True

    def check(self):
        if not self.online:
            raise OSError('offline')

    @property
    def db(self):
        return self.root / DB_REL

def generate(directory, name, frequency):
    path = directory / name
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=44100:duration=1.5',
                    '-metadata', 'artist=测试艺术家', '-metadata', 'album=测试专辑',
                    '-metadata', 'title='+path.stem, str(path)],check=True)
    return path

def exercise(stage, source=None):
    device = Replica(stage / 'replica',source.guid if source else bytes.fromhex('0011223344556677'))
    device.db.parent.mkdir(parents=True)
    if source:
        raw=source.db.read_bytes()
        sidecar=source.db.parent/'Play Counts'
        if sidecar.is_file():
            shutil.copyfile(sidecar,device.db.parent/sidecar.name)
    else:
        raw=create_empty_library('QA',WriteTarget(checksum=WriteChecksum.HASH58, firewire_guid=device.guid,
                                 database_version=0x30), supports_podcasts=True).prepared.itunes
    device.db.write_bytes(raw)
    before=IPodLibrary(raw).snapshot
    original_sidecars={p.name:p.read_bytes() for p in device.db.parent.iterdir() if p != device.db}
    sources=[generate(stage,n,f) for n,f in [('甲.mp3',320),('乙.flac',420),('丙.wav',460),('丁.m4a',480)]]
    store=stage/'backup'
    def run(paths):
        return importer.import_files(device, paths, backup_root=store)
    assert run([*sources,sources[0]]) == (4,1)
    updated=device.db.read_bytes()
    result=IPodLibrary(updated).snapshot
    assert verify_hash58(updated,device.guid)
    assert len(result.tracks)==len(before.tracks)+4 and result.playlists==before.playlists
    masters = [row.chunk for row in parse_iTunesDB(updated).find_chunks(MhypHeader) if row.chunk.header.master_flag]
    assert masters
    added_ids = {t.track_id for t in result.tracks[-4:]}
    for master in masters:
        refs = {child.header.track_id for child in master.children if isinstance(child.header, MhipHeader)}
        assert added_ids <= refs, 'new tracks missing from the firmware master playlist'
    assert [(t.track_id,t.title,t.artist,t.metadata.location) for t in result.tracks[:len(before.tracks)]] == [
        (t.track_id,t.title,t.artist,t.metadata.location) for t in before.tracks]
    for src,t in zip(sources,result.tracks[-4:]):
        dest=device.root/t.metadata.location
        assert dest.exists() and dest.stat().st_size==t.size_bytes and t.title==src.stem
        if src.suffix in ('.mp3','.flac','.m4a'):
            assert t.artist=='测试艺术家' and t.album=='测试专辑', (src.name,t.artist,t.album)
        if src.suffix in ('.mp3','.m4a'):
            assert dest.read_bytes()==src.read_bytes()
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-xerror','-i',str(dest),'-f','null','-'],check=True)
    assert all((device.db.parent/n).read_bytes()==b for n,b in original_sidecars.items())
    assert run(sources)==(0,4) and device.db.read_bytes()==updated
    assert next(store.rglob('*.bak')).read_bytes()==raw
    print('append, metadata, playlists, sidecars, decode, backup, duplicate retry OK:',len(before.tracks),'->',len(result.tracks))
    extra=generate(stage,'中断恢复.mp3',540)
    original_move=importer.move_file
    def disconnect_before_commit(src,dst,overwrite=False):
        if dst==device.db:
            device.online=False
            raise OSError('disconnected before commit')
        original_move(src,dst,overwrite)
    with patch.object(importer,'move_file',disconnect_before_commit):
        try:
            run([extra])
        except OSError:
            pass
        else:
            raise AssertionError('disconnect did not stop commit')
    assert device.db.read_bytes()==updated and list(store.rglob('pending.json'))
    device.online=True
    assert run([extra]) == (1,0) and not list(store.rglob('pending.json'))
    after=device.db.read_bytes()
    assert verify_hash58(after,device.guid) and len(IPodLibrary(after).snapshot.tracks)==len(before.tracks)+5
    assert not list(device.root.rglob('*.part')) and not list(device.root.rglob('ND-*.tmp'))
    invalid=stage/'损坏.mp3'
    invalid.write_bytes(b'not audio')
    try:
        run([extra,invalid])
    except ValueError:
        assert device.db.read_bytes()==after
    else:
        raise AssertionError('invalid audio accepted')
    print('interrupted publication, reconnect, bad input OK')

if __name__ == '__main__':
    logging.disable(logging.WARNING)
    p=argparse.ArgumentParser()
    p.add_argument('--replica-of-connected',action='store_true')
    args=p.parse_args()
    source=discover()[0][0] if args.replica_of_connected else None
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as stage:
        exercise(Path(stage),source)
