import io
import logging
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from iPodDB.library import IPodLibrary
from iPodDB.iTunesDB.writer.signature import verify_hash58
from iOpenPod.app.media.lyrics import read_lyrics_stream
from device import discover
from verify_import import Replica, generate
from importer import import_files
import edit_tx
from library_edits import edit_library, load_editor_data, signature, FORMATS

def exercise(stage,src):
    dev=Replica(stage/'virtual',src.guid)
    dev.db.parent.mkdir(parents=True)
    shutil.copyfile(src.db,dev.db)
    art='iPod_Control/Artwork'
    if (src.root/art).exists():
        shutil.copytree(src.root/art,dev.root/art)
    counts=src.db.parent/'Play Counts'
    if counts.exists():
        shutil.copyfile(counts,dev.db.parent/counts.name)
    store=stage/'backups'
    def load():
        lib=IPodLibrary(dev.db.read_bytes())
        a=dev.root/art/'ArtworkDB'
        return lib.with_artwork(a.read_bytes()) if a.exists() else lib
    original=load().snapshot
    inp=generate(stage,'NanoDrop 音乐资料测试.mp3',530)
    import_files(dev,[inp],backup_root=store)
    new=load().snapshot.tracks[-1]
    path=dev.root/new.metadata.location
    audio_before=path.read_bytes()
    buf=io.BytesIO()
    Image.new('RGB',(200,200),(239,32,64)).save(buf,format='PNG')
    words='用于测试的第一行\n用于测试的第二行'
    def enrich():
        return edit_library(dev,[signature(new)],title='核对后的曲名（现场版）',cover=buf.getvalue(),lyrics=words,backup_root=store)
    def snapshot_files():
        return {p.relative_to(dev.root).as_posix():p.read_bytes() for p in dev.root.rglob('*') if p.is_file()}
    before=snapshot_files()
    normal_move=edit_tx.move_file
    def interrupt(a,b,overwrite=False):
        if b==dev.db:
            dev.online=False
            raise OSError('simulated disconnect')
        return normal_move(a,b,overwrite)
    with patch.object(edit_tx,'move_file',interrupt):
        try:
            enrich()
        except OSError:
            assert not dev.online
        else:
            raise AssertionError('interruption missing')
    dev.online=True
    edit_tx.resume_edit(dev,next(store.glob('*')))
    assert before==snapshot_files()
    assert enrich()==1
    lib=load()
    tagged=lib.snapshot.tracks[-1]
    assert tagged.title=='核对后的曲名（现场版）' and tagged.track_id==new.track_id
    assert tagged.metadata.has_lyrics and tagged.size_bytes==path.stat().st_size
    # The nano stores the lyrics flag in the library and the text in the audio tag.
    assert tagged.artwork_id>0
    with path.open('rb') as f:
        assert read_lyrics_stream(f,path.name)==words
    assert import_files(dev,[inp],backup_root=store)==(0,1)
    for fmt in FORMATS:
        raster=lib.artwork_read(tagged.artwork_id,(fmt,),fmt.width)
        assert raster is not None
        with (dev.root/raster.relative_path).open('rb') as f:
            f.seek(raster.offset)
            pixels=raster.decode(f.read(raster.length)).rgb888
            assert pixels[0]>200 and pixels[1]<55 and pixels[2]<90
    old=original.tracks[0]
    media_before=path.read_bytes()
    assert edit_library(dev,[signature(tagged)],title='只修改曲名',backup_root=store)==1
    tagged=load().snapshot.tracks[-1]
    assert tagged.title=='只修改曲名' and path.read_bytes()==media_before
    assert tagged.metadata.has_lyrics and tagged.artwork_id and tagged.metadata.location==new.metadata.location
    details=load_editor_data(dev,tagged)
    assert details.lyrics==words and details.image and not details.notices
    assert edit_library(dev,[signature(tagged)],artist='新歌手',album='新专辑',editor_data=details,backup_root=store)==1
    tagged=load().snapshot.tracks[-1]
    assert (tagged.artist,tagged.album)==('新歌手','新专辑') and path.read_bytes()==media_before
    fresh=load_editor_data(dev,tagged)
    assert fresh.image.tobytes()==details.image.tobytes() and fresh.lyrics==words
    try:
        edit_library(dev,[signature(tagged)],album='过期编辑',editor_data=details,backup_root=store)
    except ValueError:
        pass
    else:
        raise AssertionError('outdated edit accepted')
    assert edit_library(dev,[signature(tagged)],lyrics='',editor_data=fresh,backup_root=store)==1
    tagged=load().snapshot.tracks[-1]
    assert not tagged.metadata.has_lyrics and load_editor_data(dev,tagged).lyrics==''
    assert edit_library(dev,[signature(tagged),signature(old)],remove=True,backup_root=store)==2
    final=load().snapshot
    assert [t.track_id for t in final.tracks]==[t.track_id for t in original.tracks[1:]]
    assert all(e.track_id!=old.track_id for p in final.playlists for e in p.entries)
    assert not path.exists() and verify_hash58(dev.db.read_bytes(),dev.guid)
    assert any(p.read_bytes()==audio_before for p in store.rglob(path.name))
    print('passed: interrupted edit rollback, embedded lyrics, all four decoded cover formats, multi-delete, playlists, size, backup and signature')

if __name__=='__main__':
    logging.disable(logging.WARNING)
    src=discover()[0][0]
    with tempfile.TemporaryDirectory() as tmp:
        exercise(Path(tmp),src)
