"""Offline song editor. Device writes take place after the dialog returns."""
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk
from library_edits import read_art_image


def read_text_file(path):
    with Path(path).open('rb') as stream:
        data=stream.read(1024**2+1)
    if len(data)>1024**2:
        raise ValueError('文本文件超过 1 MiB。')
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):
        text=data.decode('utf-16')
    else:
        try:
            text=data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text=data.decode('gb18030')
    if '\0' in text:
        raise ValueError('无法识别文本编码，请另存为 UTF-8 后导入。')
    return text.replace('\r\n','\n').replace('\r','\n')


def lyrics_file(path):
    text=read_text_file(path)
    if Path(path).suffix.lower()=='.lrc':
        lines=[]
        for line in text.splitlines():
            if re.fullmatch(r'\s*\[[A-Za-z][\w-]*:.*\]\s*',line):
                continue
            line=re.sub(r'(?:\[|<)\d{1,3}:\d{2}(?:[.:]\d+)?(?:\]|>)','',line)
            lines.append(line.strip())
        text='\n'.join(lines)
    if not text.strip():
        raise ValueError('文件中没有歌词。')
    return text.strip()


def manual_edit(parent,track,data,remaining=0):
    top=tk.Toplevel(parent)
    top.title('手动编辑歌曲资料')
    top.transient(parent)
    top.grab_set()
    scale=parent.winfo_fpixels('1i')/96
    width=min(round(650*scale),int(parent.winfo_screenwidth()*.92))
    height=min(round(505*scale),int(parent.winfo_screenheight()*.85))
    top.geometry(f'{width}x{height}')
    body=ttk.Frame(top,padding=14)
    body.pack(fill='both',expand=True)
    body.columnconfigure(1,weight=1)
    body.rowconfigure(5,weight=1)
    fields={}
    for i,(key,label) in enumerate((('title','曲名'),('artist','歌手'),('album','专辑'))):
        ttk.Label(body,text=label).grid(row=i,column=0,sticky='w',padx=(0,8))
        ent=ttk.Entry(body,name=key)
        ent.insert(0,getattr(track,key))
        ent.grid(row=i,column=1,columnspan=2 if key!='album' else 1,sticky='ew',pady=3)
        fields[key]=ent
    def import_album():
        path=filedialog.askopenfilename(parent=top,title='导入专辑名称',filetypes=[('文本文件','*.txt')])
        if path:
            try:
                name=read_text_file(path).strip()
                if not name or '\n' in name or len(name)>1024:
                    raise ValueError('请使用只含一行专辑名称的文本文件。')
                fields['album'].delete(0,'end')
                fields['album'].insert(0,name)
            except (ValueError,OSError) as exc:
                messagebox.showerror('专辑名称导入失败',str(exc),parent=top)
    ttk.Button(body,text='从文本导入…',command=import_album).grid(row=2,column=2,padx=(6,0))

    cover_frame=ttk.Frame(body)
    cover_frame.grid(row=4,column=0,rowspan=2,sticky='nw',padx=(0,10))
    preview=ttk.Label(cover_frame,text='暂无封面',anchor='center')
    preview.pack(pady=(6,8))
    photo=None
    cover=None
    def display(image):
        nonlocal photo
        image=image.copy()
        image.thumbnail((round(128*scale),round(128*scale)))
        photo=ImageTk.PhotoImage(image,master=top)
        preview.configure(image=photo,text='')
    if data.image is not None:
        display(data.image)
    def import_cover():
        nonlocal cover
        path=filedialog.askopenfilename(parent=top,title='选择本地封面',filetypes=[
            ('封面图片','*.jpg *.jpeg *.png *.webp *.bmp'),('所有文件','*')])
        if path:
            try:
                with open(path,'rb') as stream:
                    new=stream.read(32*1024**2+1)
                if len(new)>32*1024**2:
                    raise ValueError('封面文件超过 32 MiB。')
                display(read_art_image(new))
                cover=new
            except (ValueError,OSError) as exc:
                messagebox.showerror('封面导入失败',str(exc),parent=top)
    ttk.Button(cover_frame,text='选择封面…',command=import_cover).pack()

    bar=ttk.Frame(body)
    bar.grid(row=4,column=1,columnspan=2,sticky='ew',pady=5)
    ttk.Label(bar,text='歌词').pack(side='left')
    words=tk.Text(body,name='lyrics',height=5,width=20,wrap='word',undo=True,font=('Microsoft YaHei UI',10))
    words.grid(row=5,column=1,columnspan=2,sticky='nsew')
    scroll=ttk.Scrollbar(body,orient='vertical',command=words.yview)
    scroll.grid(row=5,column=3,sticky='ns')
    words.configure(yscrollcommand=scroll.set)
    original=(data.lyrics or '').replace('\r\n','\n').replace('\r','\n')
    words.insert('1.0',original)
    def import_lyrics():
        path=filedialog.askopenfilename(parent=top,title='导入歌词',filetypes=[('歌词或纯文本','*.lrc *.txt'),('所有文件','*')])
        if path:
            try:
                text=lyrics_file(path)
                words.delete('1.0','end')
                words.insert('1.0',text)
            except (OSError,ValueError) as exc:
                messagebox.showerror('歌词导入失败',str(exc),parent=top)
    load=ttk.Button(bar,text='导入 TXT / LRC…',command=import_lyrics)
    load.pack(side='right')
    if data.lyrics is None:
        words.configure(state='disabled')
        load.configure(state='disabled')
    if data.notices:
        ttk.Label(body,text='　'.join(data.notices),wraplength=width-45).grid(row=6,column=0,columnspan=4,sticky='w')
    bottom=ttk.Frame(body)
    bottom.grid(row=7,column=0,columnspan=4,sticky='ew',pady=(10,0))
    result=[]
    def save():
        changes={}
        for key,entry in fields.items():
            value=entry.get()
            if value!=getattr(track,key):
                changes[key]=value.strip()
        if 'title' in changes and not changes['title']:
            messagebox.showinfo('请填写曲名','歌曲名称不能为空。',parent=top)
            return
        text=words.get('1.0','end-1c')
        if data.lyrics is not None and text!=original:
            changes['lyrics']=text if text.strip() else ''
        if cover is not None:
            changes['cover']=cover
        if changes:
            result.append(changes)
        top.destroy()
    ttk.Button(bottom,text='保存到 iPod',command=save).pack(side='right')
    ttk.Button(bottom,text='跳过此曲' if remaining else '取消',command=top.destroy).pack(side='right',padx=8)
    if remaining:
        def finish():
            result.append('stop')
            top.destroy()
        ttk.Label(bottom,text=f'此后还有 {remaining} 首').pack(side='left')
        ttk.Button(bottom,text='结束编辑',command=finish).pack(side='right')
        top.protocol('WM_DELETE_WINDOW',finish)
    parent.wait_window(top)
    photo=None
    return result[0] if result else None
