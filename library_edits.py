from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import io
import shutil
import tempfile

from PIL import Image, ImageOps
from iOpenPod.app.media.lyrics import rewrite_lyrics_stream, read_lyrics_stream
from iPodDB.library import (ArtworkAsset, ArtworkPixels, CoverFormat, CoverPixelFormat,
                            FileDependency, IPodLibrary, PreparedLyrics, SourceFile, WriteResources)
from iPodDB.library.file_content import content_chunks
from iPodDB.iTunesDB.writer.signature import verify_hash58
from iPodDB.sidecars import remap_playback_sidecar

from audiofiles import digest
from importer import backup_folder, db_bytes, source_library, playback_files, recover, target, writer_lock
from edit_tx import ART, DB, commit, device_path, fingerprint, resume_edit

FORMATS=tuple(CoverFormat(i,w,w,row,CoverPixelFormat.RGB565_LE)
              for i,w,row in ((1061,55,112),(1055,128,256),(1068,128,256),(1060,320,640)))

def track_identity(track):
    return (track.track_id, track.ipod.db_track_id if track.ipod else 0, track.metadata.location)


def signature(track):
    return track_identity(track) + (track.title,track.artist,track.album,track.length_ms,track.size_bytes)

def read_art_image(data):
    with Image.open(io.BytesIO(data)) as src:
        if src.width*src.height>24_000_000:
            raise ValueError('封面图像过大。')
        img=ImageOps.exif_transpose(src).convert('RGB')
        img.thumbnail((640,640),Image.Resampling.LANCZOS)
        return img

def optional_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None

def sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None

@dataclass
class EditorData:
    revision: tuple
    lyrics: str | None
    image: Image.Image | None
    notices: tuple

def load_editor_data(device, track):
    raw=db_bytes(device)
    if raw is None:
        raise ValueError('设备曲库为空。')
    lib=source_library(device,raw)
    art=optional_bytes(device_path(device,ART))
    if art is not None:
        lib=lib.with_artwork(art)
    saved=next((t for t in lib.snapshot.tracks if t.track_id==track.track_id),None)
    if saved is None or signature(saved)!=signature(track):
        raise ValueError('所选歌曲已变化，请刷新曲库。')
    notices=[]
    words=image=None
    path=device_path(device,saved.metadata.location)
    try:
        with path.open('rb') as stream:
            words=read_lyrics_stream(stream,path.name)
    except (OSError,ValueError):
        notices.append('未能读取原歌词。可以继续修改其他资料。')
    try:
        raster=lib.artwork_read(saved.artwork_id,FORMATS,320)
        if raster is not None:
            with device_path(device,raster.relative_path).open('rb') as stream:
                stream.seek(raster.offset)
                pixels=raster.decode(stream.read(raster.length))
            image=Image.frombytes('RGB',(pixels.width,pixels.height),pixels.rgb888)
    except (OSError,ValueError):
        notices.append('原封面暂时无法预览。')
    device.check()
    return EditorData((sha(raw),sha(art)),words,image,tuple(notices))

def edit_library(device, selection, *, remove=False, cover=None, lyrics=None, title=None,
                 artist=None, album=None, editor_data=None,
                 progress=lambda msg: None, backup_root=None):
    fields={}
    for key,value in (('title',title),('artist',artist),('album',album)):
        if value is not None:
            value=value.strip()
            if (key=='title' and not value) or len(value)>1024 or any(ord(c)<32 for c in value):
                raise ValueError('曲名、歌手或专辑字段无效。')
            fields[key]=value
    has_edits=bool(fields) or cover is not None or lyrics is not None
    if not selection or (remove and has_edits):
        raise ValueError('请选择要操作的歌曲。')
    if not remove and (len(selection)!=1 or not has_edits):
        raise ValueError('请选择一首歌以及需要写入的资料。')
    folder=backup_folder(device,backup_root)
    with writer_lock(folder), tempfile.TemporaryDirectory(prefix='NanoDrop-edit-') as scratch:
        recover(device,folder/'pending.json')
        resume_edit(device,folder)
        progress('核对设备曲库和播放记录…')
        raw=db_bytes(device)
        if raw is None:
            raise ValueError('设备曲库为空。')
        lib=source_library(device,raw)
        art=optional_bytes(device_path(device,ART))
        if editor_data and editor_data.revision!=(sha(raw),sha(art)):
            raise ValueError('打开编辑窗口后设备资料已变化，请重新打开歌曲。')
        if art is not None:
            lib=lib.with_artwork(art)
        photo_path=device.root/'Photos/Photo Database'
        photo=optional_bytes(photo_path) if remove else None
        if photo is not None:
            lib=lib.with_photos(photo)
        snap=lib.snapshot
        selected={item[0]:item for item in selection}
        batch=[t for t in snap.tracks if t.track_id in selected]
        if len(batch)!=len(selected) or any(signature(t)!=selected[t.track_id] for t in batch):
            raise ValueError('选中的歌曲已发生变化，请刷新后重新选择。')
        history=playback_files(device)
        stage=Path(scratch)
        changes={}
        retired={}
        def stage_bytes(rel,old,data):
            dest=stage/('result-'+str(len(changes)))
            dest.write_bytes(data)
            changes[rel]=(old,dest)
        tracks=snap.tracks
        playlists=snap.playlists
        assets=()
        embedded=()
        lyrics_ids=()
        if remove:
            tracks=tuple(t for t in tracks if t.track_id not in selected)
            playlists=tuple(replace(p,entries=tuple(e for e in p.entries if e.track_id not in selected))
                            for p in playlists)
            retained={t.metadata.location.casefold() for t in tracks}
            for t in batch:
                rel=t.metadata.location
                if rel and rel.casefold() not in retained:
                    retired[rel]=(fingerprint(device_path(device,rel)),None)
            old_ids=tuple(t.track_id for t in snap.tracks)
            new_ids=tuple(t.track_id for t in tracks)
            for name,data in history.items():
                if data[:4] not in (b'mhdp',b'pdhm',b'mhpo',b'ophm'):
                    raise ValueError(f'不能安全调整设备的播放记录 {name}，删除已停止。')
                mapped=remap_playback_sidecar(data,old_ids,new_ids)
                if mapped!=data:
                    stage_bytes('iPod_Control/iTunes/'+name,sha(data),mapped)
        else:
            t=batch[0]
            sorts={'sort_'+key:'' for key,value in fields.items() if value!=getattr(t,key)}
            modified=replace(t,**fields,metadata=replace(t.metadata,**sorts))
            if lyrics is not None:
                rel=t.metadata.location
                if sum(x.metadata.location.casefold()==rel.casefold() for x in tracks)>1:
                    raise ValueError('多条曲目共用此音频文件，无法单独修改歌词。')
                file=device_path(device,rel)
                staged=stage/('tagged'+file.suffix)
                shutil.copyfile(file,staged)
                original=digest(staged)
                with staged.open('r+b') as stream:
                    if editor_data and read_lyrics_stream(stream,file.name)!=editor_data.lyrics:
                        raise ValueError('原歌词已变化，请重新打开歌曲。')
                    rewrite_lyrics_stream(stream,file.name,lyrics,native_compatibility=True)
                    if read_lyrics_stream(stream,file.name)!=lyrics:
                        raise ValueError('歌词回读不一致。')
                dep=FileDependency(rel,staged.stat().st_size,digest(staged))
                embedded=(PreparedLyrics(t.track_id,lyrics,dep),)
                lyrics_ids=(t.track_id,)
                modified=replace(modified,metadata=replace(modified.metadata,lyrics=lyrics))
                changes[rel]=(original,staged)
            if cover is not None:
                img=read_art_image(cover)
                assets=(ArtworkAsset(-1,ArtworkPixels(img.width,img.height,img.tobytes())),)
                modified=replace(modified,artwork_id=-1)
            tracks=tuple(modified if x.track_id==t.track_id else x for x in tracks)
        profile=replace(target(device),cover_formats=FORMATS,artwork_root_value=2)
        desired=replace(snap,tracks=tracks,playlists=playlists)
        draft=lib.begin_draft(desired,delete_omissions=remove,lyrics_updates=lyrics_ids,retag_tracks=lyrics_ids)
        plan=lib.analyze(draft,profile)
        inventory=[]
        sources=[]
        if plan.requires_artwork_inventory or assets:
            progress('核对原有封面缩略图…')
            for path in sorted((device.root/'iPod_Control/Artwork').rglob('*.ithmb')):
                rel=path.relative_to(device.root).as_posix()
                data=device_path(device,rel).read_bytes()
                dep=FileDependency(rel,len(data),sha(data))
                inventory.append(dep)
                sources.append(SourceFile(dep,data))
        res=WriteResources(lyrics=embedded,artwork=assets,files=tuple(sources),
                           file_inventory=tuple(inventory) if plan.requires_artwork_inventory or assets else None,
                           pending_playback_sidecars=False)
        progress('生成并验证设备曲库…')
        checked=lib.prepare(plan,res)
        if not checked.prepared:
            reasons=[i.message for i in checked.issues if i.severity.value=='error']
            raise ValueError('设备曲库检查未通过。\n'+'\n'.join(dict.fromkeys(reasons))[:1500])
        out=checked.prepared
        if not verify_hash58(out.itunes,device.guid):
            raise ValueError('曲库签名校验失败。')
        reread=IPodLibrary(out.itunes)
        if out.artwork is not None:
            reread=reread.with_artwork(out.artwork)
        if [t.track_id for t in reread.snapshot.tracks]!=[t.track_id for t in tracks]:
            raise ValueError('曲目表校验失败。')
        if not remove:
            saved=next(t for t in reread.snapshot.tracks if t.track_id==batch[0].track_id)
            if any(getattr(saved,key)!=value for key,value in fields.items()):
                raise ValueError('写入后的歌曲资料未通过回读校验。')
            if lyrics is not None and (bool(saved.metadata.has_lyrics)!=bool(lyrics) or saved.size_bytes!=embedded[0].file.size):
                raise ValueError('歌词标记或文件大小校验失败。')
            if cover is not None and not reread.artwork_read(saved.artwork_id,FORMATS,320):
                raise ValueError('封面索引校验失败。')
        previous={d.relative_path:d.sha256 for d in inventory}
        for file in out.artwork_files:
            rel=file.relative_path
            dest=stage/('art-'+str(len(changes)))
            with dest.open('wb') as stream:
                for block in content_chunks(file.data):
                    stream.write(block)
            changes[rel]=(previous.get(rel),dest)
        if out.artwork is not None and out.artwork!=art:
            stage_bytes(ART,sha(art),out.artwork)
        if out.photos is not None and out.photos!=photo:
            raise ValueError('选中的歌曲关联了照片资料，请先调整幻灯片配乐。')
        stage_bytes(DB,sha(raw),out.itunes)
        changes.update(retired)
        def unchanged():
            if (db_bytes(device)!=raw or playback_files(device)!=history or optional_bytes(device_path(device,ART))!=art
                or (remove and optional_bytes(photo_path)!=photo)):
                raise OSError('准备期间设备曲库发生变化，请重新选择。')
        progress('在电脑上备份本次要改动的文件…')
        commit(device,folder,changes,unchanged,progress)
        return len(batch)
