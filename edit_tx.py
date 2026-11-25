"""Recoverable device-library edits. Original files stay in a local per-device backup."""
import json
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath

from iPodDB.sidecars import is_playback_sidecar
from audiofiles import digest
from importer import move_file, write_file

DB = 'iPod_Control/iTunes/iTunesDB'
ART = 'iPod_Control/Artwork/ArtworkDB'

def device_path(device, relative):
    parts = PurePosixPath(relative).parts
    if (len(parts)<3 or '/'.join(parts)!=relative or
        any(p in ('','.','..') or re.search(r'[\\:<>"|?*\x00-\x1f]',p) for p in parts)):
        raise ValueError('曲库中的路径异常。')
    allowed = (relative in (DB, ART) or
               (parts[:2]==('iPod_Control','Music') and len(parts)>=4) or
               (parts[:2]==('iPod_Control','Artwork') and relative.lower().endswith('.ithmb')) or
               (parts[:2]==('iPod_Control','iTunes') and len(parts)==3 and is_playback_sidecar(parts[-1])))
    path=device.root.joinpath(*parts)
    if not allowed or path.resolve()!=device.root.resolve().joinpath(*parts):
        raise ValueError('媒体路径不在设备曲库内。')
    return path

def fingerprint(path):
    try:
        return digest(path)
    except FileNotFoundError:
        return None

def copy_checked(src, dest, expected):
    with src.open('rb') as a, dest.open('xb') as b:
        shutil.copyfileobj(a,b,1024**2)
        b.flush()
        os.fsync(b.fileno())
    if digest(dest)!=expected:
        raise OSError('文件回读校验失败，请检查连接。')

def resume_edit(device, folder):
    journal=folder/'edit-pending.json'
    if not journal.exists():
        return
    entry=json.loads(journal.read_text('utf8'))
    tx=entry['id']
    if not re.fullmatch('[0-9a-f]{32}',tx):
        raise ValueError('恢复记录异常。')
    backup=folder/'操作备份'/tx
    for row in reversed(entry['files']):
        device.check()
        path=device_path(device,row['path'])
        tmp=path.with_name('ND-'+tx+'.tmp')
        tmp.unlink(missing_ok=True)
        if entry['done']:
            continue
        current=fingerprint(path)
        if current==row['old']:
            continue
        if current!=row['new']:
            raise OSError('中断后设备文件又发生变化，请保留操作备份：'+str(backup))
        if row['old'] is None:
            path.unlink()
        else:
            copy_checked(backup/row['path'],tmp,row['old'])
            device.check()
            move_file(tmp,path,overwrite=current is not None)
    journal.unlink()

def commit(device, folder, changes, check_source, progress):
    """changes maps device paths to (captured original SHA or None, staged PC file or None)."""
    tx=uuid.uuid4().hex
    backup=folder/'操作备份'/tx
    journal=folder/'edit-pending.json'
    rows=[]
    required=0
    used=0
    for rel,(expected,src) in changes.items():
        device.check()
        path=device_path(device,rel)
        if fingerprint(path)!=expected:
            raise OSError('设备文件已变化，请重新载入曲库。')
        new=digest(src) if src else None
        if expected==new:
            continue
        rows.append(dict(path=rel,old=expected,new=new))
        size=src.stat().st_size if src else 0
        required=max(required,used+size)
        used+=size-(path.stat().st_size if expected is not None else 0)
        if expected is not None:
            original=backup/rel
            original.parent.mkdir(parents=True,exist_ok=True)
            copy_checked(path,original,expected)
    if not rows:
        return
    if shutil.disk_usage(device.root).free < required+64*1024:
        raise OSError('iPod 空间不足，无法暂存本次修改。')
    backup.mkdir(parents=True,exist_ok=True)
    entry=dict(id=tx,done=False,files=rows)
    data=json.dumps(entry,ensure_ascii=False).encode()
    write_file(backup/'manifest.json',data)
    staging=folder/(tx+'.json')
    write_file(staging,data)
    check_source()
    move_file(staging,journal)
    try:
        for idx,row in enumerate(rows,1):
            device.check()
            rel=row['path']
            progress(f'写入并校验设备文件 {idx}/{len(rows)}')
            path=device_path(device,rel)
            if fingerprint(path)!=row['old']:
                raise OSError('设备文件发生变化，请重新载入。')
            if row['new'] is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                tmp=path.with_name('ND-'+tx+'.tmp')
                copy_checked(changes[rel][1],tmp,row['new'])
                device.check()
                move_file(tmp,path,overwrite=row['old'] is not None)
            if fingerprint(path)!=row['new']:
                raise OSError('提交后的设备文件未通过校验。')
        entry['done']=True
        write_file(staging,json.dumps(entry).encode())
        move_file(staging,journal,overwrite=True)
    finally:
        resume_edit(device,folder)
