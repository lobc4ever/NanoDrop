import ctypes
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD
from audiofiles import EXTENSIONS
from device import discover
from importer import APP_DATA, BACKUPS, import_files, safe_media, summary
from ai_dialog import AISettingsDialog
from credentials import read_settings
from library_edits import edit_library, load_editor_data, signature, track_identity
from manual_editor import manual_edit
from music_search import MODES, SearchRequest, lookup
from review import mode_buttons, review

BG = '#f1f4f4'
WHITE = '#ffffff'
INK = '#153136'
TEAL = '#08786f'
MUTED = '#536a6d'
FONT = 'Microsoft YaHei UI'

def display_size(size):
    if size < 1024:
        return f'{size} B'
    if size < 1024**2:
        return f'{size/1024:.1f} KiB'
    return f'{size/1024**2:.2f} MiB'

def catalog_info(device):
    name, tracks, free = summary(device)
    sizes = {}
    for track in tracks:
        try:
            size = safe_media(device.root,track.metadata.location).stat().st_size
            sizes[str(track.track_id)] = size, display_size(size)
        except FileNotFoundError:
            sizes[str(track.track_id)] = None, '文件缺失'
        except (OSError, ValueError):
            sizes[str(track.track_id)] = track.size_bytes, '≈ '+display_size(track.size_bytes)
    device.check()
    return name, tracks, free, sizes

def button(parent, text, cmd, primary=False):
    return tk.Button(parent, text=text, command=cmd, font=(FONT, 10, 'bold' if primary else 'normal'),
                     bg=TEAL if primary else '#e6eded', fg=WHITE if primary else INK,
                     activebackground='#08645f' if primary else '#d5e4e2',
                     activeforeground=WHITE if primary else INK, relief='flat', borderwidth=0,
                     highlightthickness=0, cursor='hand2', padx=14, pady=6)

def label(parent, text, size=10, color=INK, bold=False, **options):
    return tk.Label(parent, text=text, fg=color, bg=parent.cget('bg'),
                    font=(FONT, size, 'bold' if bold else 'normal'), **options)

def help_connect():
    messagebox.showinfo('连接第三代 iPod nano',
        '1. 用 30 针数据线把 iPod 直接接到电脑 USB 口。\n\n'
        '2. 在「此电脑」查看是否出现新盘符。若没有，先解开 Hold 锁，按住 Menu 和中央键直至苹果标志出现，立即改按中央键和播放/暂停键，直至屏幕显示 Disk Mode。\n\n'
        '3. 如果屏幕显示 OK to disconnect，但电脑没有新盘符，请保持 Disk Mode，重新插线或换一个直连 USB 口，再点「重新检测」。')

class Window:
    def __init__(self, root):
        self.root = root
        root.title('NanoDrop 1.6.1 · iPod nano 音乐管理')
        root.iconbitmap(str(Path(__file__).with_name('NanoDrop.ico')))
        self.scale = root.winfo_fpixels('1i') / 96
        max_width, max_height = int(root.winfo_screenwidth()*.92), int(root.winfo_screenheight()*.86)
        width = min(round(840*self.scale), max_width)
        height = min(round(565*self.scale), max_height)
        root.geometry(f'{width}x{height}')
        root.minsize(min(round(650*self.scale), max_width), min(round(430*self.scale), max_height))
        root.configure(bg=BG)
        self.events = queue.Queue()
        self.paths = {}
        self.tracks = {}
        self.sizes = {}
        self.size_order = None
        self.first_connection = True
        self.matches = []
        self.pending_search = None
        self.match_after_import = tk.BooleanVar(root,value=True)
        self.search_mode=tk.StringVar(root,value='title')
        self.post_method=tk.StringVar(root,value='联网匹配')
        self.device = None
        self.scanning = self.busy = False
        self.stamp = None
        self.drives = ctypes.windll.kernel32.GetLogicalDrives()
        self.last_scan = 0
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=(FONT, 10))
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(18, 6), background='#e3eae9', foreground=MUTED)
        style.map('TNotebook.Tab', background=[('selected', WHITE)], foreground=[('selected', INK)])
        style.configure('Treeview', background=WHITE, fieldbackground=WHITE, rowheight=round(30*self.scale),
                        foreground=INK, borderwidth=0, font=(FONT, 10))
        style.configure('Treeview.Heading', background='#f3f6f5', foreground=MUTED,
                        font=(FONT, 9), relief='flat', padding=(6,5))
        style.map('Treeview', background=[('selected', '#c4e7e2')], foreground=[('selected', INK)])
        head = tk.Frame(root, bg=INK, padx=25, pady=15)
        head.pack(fill='x')
        label(head, 'NanoDrop', 22, '#ffffff', True).pack(side='left')
        label(head, 'iPod nano  第三代', 11, '#b4d6d1').pack(side='right')
        main = tk.Frame(root, bg=BG, padx=22, pady=15)
        main.pack(fill='both', expand=True)
        device_bar = tk.Frame(main, bg=WHITE, padx=15, pady=10)
        device_bar.pack(fill='x')
        self.refresh = button(device_bar, '重新检测', self.refresh_all)
        self.refresh.pack(side='right')
        self.dname = label(device_bar, '正在检测 iPod…', 13, INK, True)
        self.dname.pack(anchor='w')
        self.detail = label(device_bar, '等待 Windows 识别设备', 9, MUTED)
        self.detail.pack(anchor='w')
        search_bar=tk.Frame(main,bg=BG)
        search_bar.pack(fill='x',pady=(8,0))
        label(search_bar,'检索方式',9,MUTED).pack(side='left',padx=(0,12))
        self.mode_buttons=mode_buttons(search_bar,self.search_mode)
        self.tabs = ttk.Notebook(main)
        self.tabs.pack(fill='both', expand=True, pady=(6, 0))
        add = tk.Frame(self.tabs, bg=WHITE)
        lib = tk.Frame(self.tabs, bg=WHITE)
        self.tabs.add(add, text='待导入')
        self.tabs.add(lib, text='设备曲库')
        row = tk.Frame(add, bg=WHITE, padx=12, pady=9)
        row.pack(fill='x')
        self.add_btn = button(row, '添加歌曲…', self.choose)
        self.add_btn.pack(side='left')
        self.dir_btn = button(row, '添加文件夹…', self.choose_dir)
        self.dir_btn.pack(side='left', padx=6)
        self.rm_btn = button(row, '移出队列', self.remove)
        self.rm_btn.pack(side='right')
        post=ttk.Frame(add)
        post.pack(anchor='w',padx=14,pady=(0,4))
        self.match_check=ttk.Checkbutton(post,text='导入后逐首处理',variable=self.match_after_import,command=self.sync_buttons)
        self.match_check.pack(side='left')
        self.post_picker=ttk.Combobox(post,state='readonly',width=10,values=('联网匹配','手动编辑'),textvariable=self.post_method)
        self.post_picker.pack(side='left',padx=6)
        self.pending = self.tree(add, [('file','文件名',330),('fmt','格式',65),('size','大小',85)])
        actions = tk.Frame(lib, bg=WHITE, padx=12, pady=9)
        actions.pack(fill='x')
        self.search_btn = button(actions, '联网匹配歌曲资料…', self.search_track, True)
        self.search_btn.pack(side='left')
        self.edit_btn=button(actions,'手动编辑…',self.edit_selected)
        self.edit_btn.pack(side='left',padx=6)
        self.delete_btn = button(actions, '删除所选歌曲…', self.delete_tracks)
        self.delete_btn.pack(side='right')
        self.catalog = self.tree(lib, [('title','歌曲',220),('artist','艺术家',125),('album','专辑',154),('time','时长',55),('size','大小',90)])
        self.catalog.heading('size',command=self.sort_size)
        self.catalog.bind('<<TreeviewSelect>>',self.selected_tracks)
        self.empty = tk.Frame(add, bg=WHITE)
        drop_title = label(self.empty, '把歌曲或文件夹拖到这里', 14, INK, True)
        drop_title.pack()
        drop_hint = label(self.empty, '支持 MP3、M4A、FLAC、WAV 等本地音频', 10, MUTED)
        drop_hint.pack(pady=(7,0))
        self.empty.place(relx=.5, rely=.57, anchor='center')
        self.libempty = label(lib, '连接 iPod 后显示设备曲库', 12, MUTED)
        self.libempty.place(relx=.5, rely=.5, anchor='center')
        for widget in (root, add, self.empty, drop_title, drop_hint, self.pending):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self.drop)
        foot = tk.Frame(main, bg=BG, pady=10)
        foot.pack(side='bottom', fill='x', before=self.tabs)
        self.start_btn = button(foot, '导入到 iPod', self.start, True)
        self.start_btn.pack(side='right')
        self.status = label(foot, '先选择要导入的本地音乐。', 10, MUTED, anchor='w')
        self.status.pack(side='left', fill='x', expand=True, padx=(0,8))
        menu = tk.Menu(root)
        tools_menu = tk.Menu(menu, tearoff=False)
        tools_menu.add_command(label='连接帮助', command=help_connect)
        tools_menu.add_command(label='打开曲库备份文件夹', command=self.open_backups)
        tools_menu.add_command(label='搜索 AI 设置（API Key）…', command=self.configure_key)
        tools_menu.add_command(label='关于 NanoDrop', command=lambda: messagebox.showinfo(
            '关于 NanoDrop', 'NanoDrop 1.6.1 · Windows x64\n适用于第三代 iPod nano 的音乐管理工具。\n\n'
            '曲库引擎来自 iOpenPod 2.1.4（John Gibbons）。音频读取及转换使用 Mutagen 和 FFmpeg。'
            '\n\nNanoDrop 采用 GPL-3.0-or-later 许可证。随附源码包含构建说明与第三方许可。'))
        menu.add_cascade(label='工具', menu=tools_menu)
        root.configure(menu=menu)
        self.tabs.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.sync_buttons()
        root.after(80, self.drain)
        root.after(180, self.scan)
        root.after(1000, self.poll)

    def tree(self, parent, columns):
        wrap = tk.Frame(parent, bg=WHITE)
        wrap.pack(fill='both', expand=True, padx=10, pady=(5, 7))
        tree = ttk.Treeview(wrap, show='headings', height=4, columns=[c[0] for c in columns])
        for key, title, width in columns:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=round(width*self.scale), minwidth=round(45*self.scale), anchor='w')
        bar = ttk.Scrollbar(wrap, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    def worker(self, func, event):
        def run():
            try:
                self.events.put((event, func()))
            except Exception as exc:
                logging.exception(event)
                self.events.put((event+'-error', str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def scan(self):
        if self.scanning or self.busy:
            return
        self.scanning = True
        self.last_scan = time.monotonic()
        self.sync_buttons()
        cached = self.stamp
        def read():
            devices, seen = discover()
            if len(devices) > 1:
                raise ValueError('检测到多台 iPod，请只连接本次要导入的那一台。')
            if not devices:
                return None, seen, None, None
            device = devices[0]
            stat = device.db.stat() if device.db.exists() else None
            stamp = (device, (stat.st_size, stat.st_mtime_ns) if stat else None)
            return device, seen, stamp, catalog_info(device) if stamp != cached else None
        self.worker(read, 'scan')

    def refresh_all(self):
        self.stamp = None
        self.scan()

    def poll(self):
        drives = ctypes.windll.kernel32.GetLogicalDrives()
        if not (self.scanning or self.busy) and (drives != self.drives or time.monotonic()-self.last_scan > 40):
            self.drives = drives
            self.scan()
        self.root.after(1000, self.poll)

    def sync_buttons(self):
        self.match_check.configure(state='disabled' if self.busy else 'normal')
        self.post_picker.configure(state='readonly' if self.match_after_import.get() and not self.busy else 'disabled')
        for control in self.mode_buttons:
            control.configure(state='disabled' if self.busy else 'normal')
        self.start_btn.configure(state='normal' if self.paths and self.device and not (self.busy or self.scanning) else 'disabled')
        self.refresh.configure(state='disabled' if self.busy or self.scanning else 'normal')
        for btn in (self.add_btn, self.dir_btn, self.rm_btn):
            btn.configure(state='disabled' if self.busy else 'normal')
        selected = len(self.catalog.selection())
        ready = self.device and not (self.busy or self.scanning)
        self.search_btn.configure(state='normal' if self.device and not self.busy and selected==1 else 'disabled')
        self.edit_btn.configure(state='normal' if ready and selected==1 else 'disabled')
        self.delete_btn.configure(state='normal' if ready and selected else 'disabled')

    def tab_changed(self, event=None):
        if self.tabs.index('current') == 1:
            self.start_btn.pack_forget()
            if not self.busy:
                self.selected_tracks()
        else:
            self.start_btn.pack(side='right')
            if not self.busy:
                self.queue_status()

    def selected_tracks(self, event=None):
        ids = self.catalog.selection()
        self.sync_buttons()
        if self.busy or self.scanning or self.tabs.index('current') != 1:
            return
        if len(ids) == 1:
            size, desc = self.sizes[ids[0]]
            detail = f'{desc}（{size:,} 字节）' if size is not None else desc
            self.status.configure(text=f'已选 1 首  ·  {detail}')
        elif ids:
            self.status.configure(text=f'已选 {len(ids)} 首歌曲')
        else:
            self.status.configure(text='选择一首歌检索资料，按住 Ctrl 或 Shift 可多选删除。')

    def sort_size(self, toggle=True):
        if toggle:
            self.size_order = not self.size_order
        if self.size_order is None:
            return
        self.catalog.heading('size',text='大小 ↓' if self.size_order else '大小 ↑')
        ids = self.catalog.get_children()
        available = [k for k in ids if self.sizes[k][0] is not None]
        available.sort(key=lambda k:self.sizes[k][0],reverse=self.size_order)
        for i,k in enumerate(available+[k for k in ids if self.sizes[k][0] is None]):
            self.catalog.move(k,'',i)

    def drop(self, event):
        if not self.busy:
            self.add_paths(self.root.tk.splitlist(event.data))
        return 'copy'

    def choose(self):
        pattern = ' '.join('*'+ext for ext in sorted(EXTENSIONS))
        files = filedialog.askopenfilenames(title='选择本地音乐', filetypes=[('音频文件',pattern),('所有文件','*.*')])
        if files:
            self.add_paths(files)

    def choose_dir(self):
        directory = filedialog.askdirectory(title='选择音乐文件夹')
        if directory:
            self.add_paths([directory])

    def add_paths(self, values):
        skipped = 0
        for value in values:
            path = Path(value)
            for item in (path.rglob('*') if path.is_dir() else (path,)):
                if not item.is_file():
                    continue
                if item.suffix.lower() not in EXTENSIONS:
                    skipped += 1
                    continue
                key = str(item.resolve())
                if key not in self.paths:
                    self.paths[key] = item
                    self.pending.insert('', 'end', iid=key, values=(item.name,item.suffix[1:].upper(),display_size(item.stat().st_size)))
        self.tabs.select(0)
        self.queue_status()
        if skipped:
            self.status.configure(text=f'已选 {len(self.paths)} 首，跳过 {skipped} 个不支持的文件。')

    def queue_status(self):
        if self.paths:
            self.empty.place_forget()
        else:
            self.empty.place(relx=.5,rely=.57,anchor='center')
        self.tabs.tab(0, text=f'待导入  {len(self.paths)}')
        self.status.configure(text=f'已选择 {len(self.paths)} 首歌曲。' if self.paths else '先选择要导入的本地音乐。')
        self.sync_buttons()

    def remove(self):
        for key in self.pending.selection():
            del self.paths[key]
            self.pending.delete(key)
        self.queue_status()

    def start(self):
        if not self.device or not self.paths or self.busy or self.scanning:
            return
        self.busy = True
        self.sync_buttons()
        device, paths = self.device, tuple(self.paths.values())
        match=self.match_after_import.get()
        def run():
            tracks=[]
            restored=[]
            counts=import_files(device,paths,self.progress,imported_tracks=tracks if match else None,restored_paths=restored)
            return counts,device,tracks,len(restored)
        self.worker(run,'import')

    def progress(self, message):
        self.events.put(('progress',message))

    def configure_key(self):
        try:
            profile = read_settings()
        except (ValueError, KeyError, OSError) as exc:
            messagebox.showerror('读取搜索 AI 设置失败',str(exc),parent=self.root)
            return
        result = AISettingsDialog(self.root, profile).result
        if result:
            self.status.configure(text=result)

    def delete_tracks(self):
        ids = self.catalog.selection()
        if not self.device or self.busy or self.scanning or not ids:
            return
        device = self.device
        tracks = [self.tracks[k] for k in ids]
        picked = [signature(t) for t in tracks]
        names = '\n'.join('· '+(t.title or '无标题') for t in tracks[:4])
        if len(tracks)>4:
            names += f'\n… 等 {len(tracks)} 首'
        if not messagebox.askyesno('从 iPod 删除歌曲',f'{names}\n\n将从 iPod 曲库、播放列表和设备存储中移除所选的 {len(tracks)} 首歌曲。原文件会先备份到电脑。',parent=self.root,default='no'):
            return
        self.busy = True
        self.sync_buttons()
        self.worker(lambda:edit_library(device,picked,remove=True,progress=self.progress),'delete')

    def search_track(self):
        ids = self.catalog.selection()
        if not self.device or self.busy or len(ids)!=1:
            return
        track = self.tracks[ids[0]]
        if self.scanning:
            self.pending_search = (self.device, track_identity(track), self.search_mode.get())
            self.status.configure(text='正在确认设备，随后检索 · '+track.title)
            return
        self.begin_search(self.device,track)

    def finish_pending_search(self):
        if self.pending_search is None:
            return
        device, identity, mode = self.pending_search
        self.pending_search = None
        ids = self.catalog.selection()
        if device == self.device and self.tabs.index('current') == 1 and len(ids)==1:
            track = self.tracks[ids[0]]
            if track_identity(track) == identity:
                self.begin_search(device, track, SearchRequest.for_track(track, mode))
                return
        self.status.configure(text='设备或所选歌曲已变更，请重新选择要检索的歌曲。')

    def edit_selected(self):
        ids=self.catalog.selection()
        if self.device and not (self.busy or self.scanning) and len(ids)==1:
            self.begin_edit(self.device,self.tracks[ids[0]])

    def begin_edit(self,device,track):
        self.busy=True
        self.sync_buttons()
        self.status.configure(text='正在读取现有封面与歌词 · '+track.title)
        self.worker(lambda:(device,track,load_editor_data(device,track)),'editor')

    def begin_search(self,device,track,request=None):
        request=request or SearchRequest.for_track(track,self.search_mode.get())
        self.busy = True
        self.sync_buttons()
        self.status.configure(text=MODES[request.mode]+'检索 · '+request.term)
        def run():
            device.check()
            return device,track,lookup(track,self.progress,request),request
        self.worker(run,'lookup')

    def next_match(self):
        if self.matches:
            device,track=self.matches.pop(0)
            if self.post_method.get()=='手动编辑':
                self.begin_edit(device,track)
            else:
                self.begin_search(device,track)
            return True
        return False

    def review_result(self, device, track, result, request):
        answer = review(self.root,track,result,remaining=len(self.matches),request=request)
        if isinstance(answer,SearchRequest):
            self.begin_search(device,track,answer)
            return
        self.apply_edit(device,track,answer)

    def apply_edit(self,device,track,answer,editor_data=None):
        if answer=='stop':
            self.matches.clear()
        if answer is None or answer=='stop':
            if self.next_match():
                return
            self.busy = False
            self.sync_buttons()
            self.status.configure(text='核对已结束。')
            self.refresh_all()
            return
        self.worker(lambda:edit_library(device,[signature(track)],**answer,editor_data=editor_data,progress=self.progress),'enrich')

    def clear_catalog(self):
        self.device = self.stamp = None
        self.tracks.clear()
        self.sizes.clear()
        self.catalog.delete(*self.catalog.get_children())
        self.tabs.tab(1, text='设备曲库')
        self.libempty.configure(text='连接 iPod 后显示设备曲库')
        self.libempty.place(relx=.5, rely=.5, anchor='center')

    def drain(self):
        while not self.events.empty():
            event, data = self.events.get()
            if event.startswith('scan'):
                self.scanning = False
                if event == 'scan-error':
                    self.clear_catalog()
                    self.dname.configure(text='设备尚未就绪')
                    self.detail.configure(text=data[:75])
                else:
                    device, seen, stamp, stats = data
                    if device is None:
                        self.clear_catalog()
                        self.dname.configure(text='等待 iPod 盘符' if seen else '等待连接 iPod nano')
                        self.detail.configure(text='Windows 已识别 USB 设备，正在等待磁盘就绪。' if seen else '连接后会自动检测。连接遇到问题时可查看「工具 → 连接帮助」。')
                    else:
                        same_device = self.device == device
                        self.device, self.stamp = device, stamp
                        if stats:
                            name, tracks, free, self.sizes = stats
                            selected = self.catalog.selection() if same_device else ()
                            focus = self.catalog.focus()
                            previous = self.tracks
                            self.tracks = {str(t.track_id):t for t in tracks}
                            self.dname.configure(text=f'{name}  ·  {device.root.drive}')
                            self.detail.configure(text=f'第三代 iPod nano    ·    {len(tracks)} 首    ·    可用 {free/1024**3:.2f} GB')
                            self.catalog.delete(*self.catalog.get_children())
                            for t in tracks:
                                sec = t.length_ms//1000
                                k = str(t.track_id)
                                self.catalog.insert('', 'end', iid=k, values=(t.title,t.artist,t.album,f'{sec//60}:{sec%60:02d}',self.sizes[k][1]))
                            self.sort_size(toggle=False)
                            def survives(k):
                                return k in previous and k in self.tracks and track_identity(previous[k]) == track_identity(self.tracks[k])
                            self.catalog.selection_set([k for k in selected if survives(k)])
                            if same_device and survives(focus):
                                self.catalog.focus(focus)
                                self.catalog.see(focus)
                            self.tabs.tab(1, text=f'设备曲库  {len(tracks)}')
                            if tracks:
                                self.libempty.place_forget()
                                if self.first_connection and not self.paths:
                                    self.tabs.select(1)
                                self.first_connection = False
                            else:
                                self.libempty.configure(text='设备曲库为空')
                                self.libempty.place(relx=.5,rely=.5,anchor='center')
                self.sync_buttons()
                self.finish_pending_search()
            elif event == 'progress':
                self.status.configure(text=data[:64])
            elif event == 'lookup':
                self.review_result(*data)
            elif event == 'editor':
                device,track,details=data
                answer=manual_edit(self.root,track,details,remaining=len(self.matches))
                self.apply_edit(device,track,answer,details)
            else:
                self.busy = False
                if event.endswith('-error'):
                    self.matches.clear()
                    name = {'import':'导入','delete':'删除','enrich':'写入歌曲资料','lookup':'检索资料','editor':'读取歌曲资料'}[event.split('-')[0]]
                    self.status.configure(text=f'{name}未完成。')
                    messagebox.showerror(name+'未完成',data,parent=self.root)
                elif event == 'import':
                    self.paths.clear()
                    self.pending.delete(*self.pending.get_children())
                    self.queue_status()
                    (written,skipped),device,new_tracks,restored = data
                    detail=f'，补回 {restored} 个缺失文件' if restored else ''
                    self.status.configure(text=f'已导入 {written-restored} 首{detail}，跳过 {skipped} 首重复歌曲。请从 Windows 安全弹出。')
                    self.matches=[(device,t) for t in new_tracks]
                    if self.next_match():
                        continue
                elif event == 'delete':
                    self.status.configure(text=f'已从 iPod 删除 {data} 首歌曲，原文件已备份到电脑。')
                elif event == 'enrich':
                    self.status.configure(text='所选资料已写入 iPod 并通过回读校验。')
                    if self.next_match():
                        continue
                self.refresh_all()
        self.root.after(90, self.drain)

    def open_backups(self):
        BACKUPS.mkdir(parents=True,exist_ok=True)
        os.startfile(BACKUPS)

    def close(self):
        if self.busy:
            messagebox.showinfo('操作进行中', '请等待当前操作结束后再关闭窗口。')
        else:
            self.root.destroy()

if __name__ == '__main__':
    ctypes.windll.user32.SetProcessDPIAware()
    APP_DATA.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=APP_DATA/'NanoDrop.log', level=logging.ERROR,
                        format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    root = TkinterDnD.Tk()
    window = Window(root)
    if len(sys.argv) > 1:
        window.add_paths(sys.argv[1:])
    root.mainloop()
