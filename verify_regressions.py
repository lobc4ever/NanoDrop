"""Portable offline checks for the 1.6 importer and catalog changes."""
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from credentials import SearchAI
import music_search as search
from audiofiles import digest
from importer import import_files, windows_path
from iOpenPod.app.media.lyrics import read_lyrics_stream, rewrite_lyrics_stream
from iPodDB.library import IPodLibrary
from verify_import import Replica, generate


def check_import(root):
    dev = Replica(root / 'device', bytes.fromhex('0011223344556677'))
    dev.root.mkdir()
    store = root / ('backup-' + 'a' * 155)
    files = [generate(root, name, hz) for name,hz in [('song.mp3',440),('other.m4a',500)]]
    for src in files:
        with src.open('r+b') as stream:
            rewrite_lyrics_stream(stream, src.name, 'original lyrics', native_compatibility=True)
    assert import_files(dev,files,backup_root=store)==(2,0)
    db = dev.db.read_bytes()
    tracks = IPodLibrary(db).snapshot.tracks
    media = [dev.root / t.metadata.location for t in tracks]
    for t,src,path in zip(tracks,files,media):
        assert t.metadata.has_lyrics and digest(path)==digest(src)
        with path.open('rb') as stream:
            assert read_lyrics_stream(stream,path.name)=='original lyrics'
    media[0].unlink()
    restored, added = [], []
    assert import_files(dev,[files[0],files[0]],backup_root=store,
                        imported_tracks=added,restored_paths=restored)==(1,1)
    assert not added and restored==[tracks[0].metadata.location]
    assert dev.db.read_bytes()==db and digest(media[0])==digest(files[0])
    media[0].unlink()
    fresh=generate(root,'new.mp3',620)
    assert import_files(dev,[files[0],fresh],backup_root=store,imported_tracks=added)==(2,0)
    result=IPodLibrary(dev.db.read_bytes()).snapshot.tracks
    assert len(result)==3 and len(added)==1 and result[0].track_id==tracks[0].track_id
    assert digest(media[0])==digest(files[0])
    assert any(len(str(p).removeprefix('\\\\?\\'))>260 and p.read_bytes()==db
               for p in store.rglob('*.bak'))


def check_search():
    release={'id':'00000000-0000-0000-0000-000000000011','title':'同专辑'}
    records=[dict(title=t,length=200000,releases=[release],**{'artist-credit':[{'name':'甲'}]})
             for t in ('第一首','第二首','第一首')]
    with patch.object(search,'query',return_value={'recordings':records}):
        assert [c.title for c in search.archive_choices('','甲')]==['第一首','第二首']
    netease={'code':200,'result':{'songs':[{'name':'十年','ar':[{'name':'陈奕迅'}],'dt':205423,
            'al':{'name':'黑白灰','picUrl':'http://p1.music.126.net/cover.jpg'}}]}}
    qq={'code':0,'data':{'song':{'list':[{'songname':'十年','singer':[{'name':'陈奕迅'}],
            'albumname':'黑白灰','interval':205,'albummid':'000GDz8k03UOaI'}]}}}
    track=SimpleNamespace(title='十年',artist='陈奕迅',album='黑白灰',length_ms=205000)
    deezer=search.Candidate(track.title,track.artist,track.album,205,'Deezer','')
    archive=search.Candidate(track.title,track.artist,track.album,205,'Cover Art Archive','artwork')
    def catalog(url, params):
        if 'music.163.com' in url:
            return netease
        if 'y.qq.com' in url:
            return qq
        raise AssertionError(url)
    with patch.object(search,'query',side_effect=catalog), patch.object(search,'read_settings',return_value=SearchAI()), \
         patch.object(search,'cover_choices',return_value=[deezer]), patch.object(search,'lyric_choices',return_value=[]):
        for mode in ('title','artist'):
            rows,_=search.lookup(track,lambda _:None,search.SearchRequest.for_track(track,mode))
            assert len(rows)==1 and set(rows[0].sources)=={'网易云音乐','QQ 音乐','Deezer'}
            assert rows[0].cover.value.startswith('https://p1.music.126.net/')
            assert rows[0].alternate_covers[0].value.startswith('https://y.gtimg.cn/')
        with patch.object(search,'netease_choices',side_effect=OSError):
            rows,note=search.lookup(track,lambda _:None)
            assert rows[0].cover.source=='QQ 音乐' and '网易云音乐 查询失败' in note
        with patch.object(search,'netease_choices',return_value=[]), patch.object(search,'qq_choices',return_value=[]), \
             patch.object(search,'archive_choices',return_value=[archive]):
            rows,_=search.lookup(track,lambda _:None)
            assert rows[0].cover==archive
            assert search.complete_recording(search.Recording(deezer)).cover==archive


if __name__=='__main__':
    logging.disable(logging.WARNING)
    with tempfile.TemporaryDirectory(dir=windows_path(tempfile.gettempdir())) as tmp:
        check_import(Path(tmp))
    check_search()
    print('Passed: restore, mixed import, MP3/M4A lyrics, long backups, shared releases, cover fallback, domestic search and provider failure.')
