# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

here = Path(SPECPATH)
engine = here / 'vendor'
if not engine.is_dir():
    engine = here.parent / 'iopenpod' / 'src'
lic = here / 'licenses'
writer = engine / 'iPodDB/iTunesDB/writer'
data = [(str(here / 'NanoDrop.ico'), '.'),
        (str(writer / 'calcHashAB.NOTICE'), 'licenses'),
        (str(writer / 'calcHashAB.wasm.b64'), 'iPodDB/iTunesDB/writer')]
data += [(str(f), 'licenses/' + f.parent.relative_to(lic).as_posix()) for f in lic.rglob('*') if f.is_file()]
data += collect_data_files('tzdata')
a = Analysis([str(here / 'main.py')], pathex=[str(here), str(engine)],
             binaries=collect_dynamic_libs('wasmtime'),
             datas=data, hiddenimports=['imageio_ffmpeg.binaries'], excludes=['IPython','matplotlib','pytest'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='NanoDrop',
          icon=str(here/'NanoDrop.ico'), console=False, upx=False)
