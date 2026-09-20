# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
block_cipher = None
a = Analysis(['main.py'], pathex=[], binaries=[], datas=[('index.html','.'),('styles.css','.'),('app.js','.')], hiddenimports=['uvicorn.logging','uvicorn.loops.auto','uvicorn.protocols.http.auto','uvicorn.protocols.websockets.auto','fastapi','starlette'], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], win_no_prefer_redirects=False, win_private_assemblies=False, cipher=block_cipher)
pyz=PYZ(a.pure,a.zipped_data,cipher=block_cipher)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='AutoTech_Europe',icon='assets/autotech_europe.ico',debug=False,bootloader_ignore_signals=False,strip=False,upx=True,console=True)