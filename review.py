import queue
import threading
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
from library_edits import read_art_image
from music_search import MODES, SearchRequest, archive_choices, complete_recording, cover_bytes, identity, same_release


def mode_buttons(parent,variable,command=None):
    buttons=[]
    for mode,label in MODES.items():
        button=ttk.Radiobutton(parent,text=label,value=mode,variable=variable,command=command)
        button.pack(side='left',padx=(0,8))
        buttons.append(button)
    return buttons


def review(parent,track,result,remaining=0,request=None):
    rows,note=result
    request=request or SearchRequest.for_track(track,'title')
    top=tk.Toplevel(parent)
    top.title('核对候选歌曲')
    top.transient(parent)
    top.grab_set()
    scale=parent.winfo_fpixels('1i')/96
    width=min(round(835*scale),int(parent.winfo_screenwidth()*.94))
    height=min(round(650*scale),int(parent.winfo_screenheight()*.87))
    top.geometry(f'{width}x{height}')
    top.columnconfigure(0,weight=1)
    top.rowconfigure(1,weight=2)
    top.rowconfigure(4,weight=2)
    head=ttk.Frame(top,padding=(14,10,14,4))
    head.grid(row=0,column=0,sticky='ew')
    ttk.Label(head,text='当前曲名　'+track.title,wraplength=width-44,font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w')
    detail=f'当前艺术家　{track.artist or "—"}　　当前专辑　{track.album or "—"}'
    secs=track.length_ms//1000
    ttk.Label(head,text=f'{detail}   {secs//60}:{secs%60:02d}　　{note}',wraplength=width-44).pack(anchor='w')
    search=ttk.Frame(head,padding=(0,5,0,0))
    search.pack(fill='x')
    mode=tk.StringVar(top,value=request.mode)
    term=tk.StringVar(top,value=request.term)
    def switch():
        term.set(SearchRequest.for_track(track,mode.get()).term)
        entry.focus_set()
    mode_buttons(search,mode,switch)
    entry=ttk.Entry(search,name='keyword',textvariable=term)
    entry.pack(side='left',fill='x',expand=True,padx=5)

    filter_bar=ttk.Frame(head,padding=(0,5,0,0))
    filter_bar.pack(fill='x')
    ttk.Label(filter_bar,text='筛选当前结果').pack(side='left')
    filter_text=tk.StringVar(top)
    filter_entry=ttk.Entry(filter_bar,name='result_filter',textvariable=filter_text)
    filter_entry.pack(side='left',fill='x',expand=True,padx=6)
    clear_filter=ttk.Button(filter_bar,text='清空',width=5,command=lambda:filter_text.set(''))
    clear_filter.pack(side='left')
    count_label=ttk.Label(filter_bar,width=12,anchor='e')
    count_label.pack(side='right',padx=(6,0))

    table=ttk.Frame(top,padding=(14,4,14,0))
    table.grid(row=1,column=0,sticky='nsew')
    columns=[('title','候选曲名',160),('artist','艺术家',95),('album','专辑',125),('time','时长',48),('source','目录来源',175),('data','封面 / 歌词',84)]
    tree=ttk.Treeview(table,show='headings',columns=[c[0] for c in columns],selectmode='browse',height=5)
    for col,label,size in columns:
        tree.column(col,width=round(size*scale),minwidth=38,anchor='w')
        tree.heading(col,text=label,anchor='w')
    searchable=[]
    for i,row in enumerate(rows):
        song=row.entry
        searchable.append(identity(' '.join(filter(None,(song.title,song.artist,song.album)))))
        length=f'{song.duration//60}:{song.duration%60:02d}' if song.duration else '—'
        assets='、'.join(v for v,available in (('封面',row.cover),('歌词',row.lyric)) if available)
        sources='、'.join(row.sources) or song.source
        tree.insert('','end',iid=str(i),values=(song.title,song.artist or '',song.album or '',length,sources,assets or '—'))
    scroll=ttk.Scrollbar(table,orient='vertical',command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y')
    tree.pack(fill='both',expand=True)

    target=ttk.Label(top,text='请选择与这首歌对应的候选版本。' if rows else '没有找到候选歌曲。',wraplength=width-48)
    target.grid(row=2,column=0,sticky='w',padx=14,pady=(8,0))
    options=ttk.Frame(top,padding=(14,6))
    options.grid(row=3,column=0,sticky='ew')
    text_fields={'title':'曲名','artist':'艺术家名称','album':'专辑名'}
    option_labels={k:'更新'+label for k,label in text_fields.items()}
    option_labels.update(cover='写入封面',lyrics='写入歌词')
    flags={key:tk.BooleanVar(top) for key in option_labels}
    pane=ttk.Frame(top,padding=(14,0,14,4))
    pane.grid(row=4,column=0,sticky='nsew')
    pane.columnconfigure(1,weight=1)
    pane.rowconfigure(0,weight=1)
    art=ttk.Label(pane,text='封面预览',anchor='center',width=18)
    art.grid(row=0,column=0,sticky='n',padx=(0,10))
    words=tk.Text(pane,wrap='word',height=3,width=24,font=('Microsoft YaHei UI',10),padx=7,pady=6,state='disabled')
    words.grid(row=0,column=1,sticky='nsew')
    scroll=ttk.Scrollbar(pane,orient='vertical',command=words.yview)
    words.configure(yscrollcommand=scroll.set)
    scroll.grid(row=0,column=2,sticky='ns')
    bottom=ttk.Frame(top,padding=(14,4,14,10))
    bottom.grid(row=5,column=0,sticky='ew')
    if remaining:
        ttk.Label(bottom,text=f'此后还有 {remaining} 首').pack(side='left')
    current=None
    photos={}
    pending=set()
    completing=set()
    checked=set()
    responses=queue.Queue()
    selection=[]
    timer=None
    options_touched=False
    def dismiss():
        if timer is not None:
            top.after_cancel(timer)
        top.destroy()
    top.protocol('WM_DELETE_WINDOW',dismiss)
    def accept():
        if current is None or ok.instate(['disabled']) or tree.selection()!=(str(current),) or mode.get()!=request.mode or term.get().strip()!=request.term:
            return
        row=rows[current]
        changes={key:getattr(row.entry,key).strip() for key in text_fields if flags[key].get()}
        if flags['cover'].get():
            changes['cover']=photos[current][0]
        if flags['lyrics'].get():
            changes['lyrics']=row.lyric.value
        selection.append(changes)
        dismiss()
    def research(event=None):
        value=term.get().strip()
        if value:
            selection.append(SearchRequest(mode.get(),value))
            dismiss()
    retry=ttk.Button(search,text='重新检索',command=research)
    retry.pack(side='right')
    entry.bind('<Return>',research)
    ok=ttk.Button(bottom,text='确认写入 iPod',command=accept,state='disabled')
    ok.pack(side='right')
    ttk.Button(bottom,text='跳过此曲' if remaining else '取消',command=dismiss).pack(side='right',padx=8)
    def finish():
        selection.append('stop')
        dismiss()
    if remaining:
        ttk.Button(bottom,text='结束核对',command=finish).pack(side='right')
        top.protocol('WM_DELETE_WINDOW',finish)

    def refresh_options():
        retry.configure(state='normal' if term.get().strip() else 'disabled')
        if mode.get()!=request.mode or term.get().strip()!=request.term:
            target.configure(text='请重新检索，查看与当前关键词对应的候选。')
            ok.configure(state='disabled')
            return
        if current is None:
            if not rows:
                hint='没有找到候选歌曲，可修改上方检索词。'
            elif not tree.get_children():
                hint='没有符合筛选条件的候选，可修改或清空筛选词。'
            else:
                hint='请选择与这首歌对应的候选版本。'
            target.configure(text=hint)
            ok.configure(state='disabled')
            return
        row=rows[current]
        values=[]
        for key,label in text_fields.items():
            value=getattr(row.entry,key).strip() if flags[key].get() else getattr(track,key)
            values.append(f'{label}　{value or "—"}')
        target.configure(text='写入后　'+'　·　'.join(values))
        ready=any(flag.get() for flag in flags.values()) and (
            not flags['cover'].get() or (current in photos and photos[current][0]))
        ok.configure(state='normal' if ready else 'disabled')
    def change_option():
        nonlocal options_touched
        options_touched=True
        refresh_options()
    checks={}
    for key,label in option_labels.items():
        cb=ttk.Checkbutton(options,text=label,variable=flags[key],command=change_option,state='disabled')
        cb.pack(side='left',padx=(0,12))
        checks[key]=cb

    def fetch_cover(index):
        row=rows[index]
        tried=set()
        def preview(candidates):
            for candidate in candidates:
                if candidate.value in tried:
                    continue
                tried.add(candidate.value)
                try:
                    data=cover_bytes(candidate)
                    img=read_art_image(data)
                except (ValueError,OSError):
                    continue
                img.thumbnail((round(145*scale),round(145*scale)))
                responses.put(('image',index,data,img))
                return True
            return False
        try:
            if preview((row.cover,*row.alternate_covers)):
                return
            candidates=archive_choices(row.entry.title,row.entry.artist)
            if preview(c for c in candidates if same_release(row.entry,c)):
                return
        except Exception:
            pass
        responses.put(('image',index,None,None))

    def load_assets(index):
        row=rows[index]
        try:
            row=complete_recording(row)
        finally:
            responses.put(('assets',index,row,None))

    def show_cover():
        if current in photos:
            data,img=photos[current]
            art.configure(image=img or '',text='' if data else '封面下载失败')
            if not data:
                flags['cover'].set(False)
                checks['cover'].configure(state='disabled')
        elif rows[current].cover:
            art.configure(image='',text='正在读取封面…')
            if current not in pending:
                pending.add(current)
                threading.Thread(target=fetch_cover,args=(current,),daemon=True).start()
        else:
            art.configure(image='',text='此版本暂无封面')
        refresh_options()

    def display(initial):
        row=rows[current]
        available={}
        for key in text_fields:
            candidate=(getattr(row.entry,key) or '').strip()
            available[key]=bool(candidate) and candidate!=getattr(track,key)
        available.update(cover=bool(row.cover),lyrics=bool(row.lyric))
        for key,enabled in available.items():
            check=checks[key]
            if initial or not enabled or (not options_touched and check.instate(['disabled'])):
                flags[key].set(enabled)
            check.configure(state='normal' if enabled else 'disabled')
        words.configure(state='normal')
        words.delete('1.0','end')
        words.insert('1.0',row.lyric.value if row.lyric else ('正在查找此版本的歌词…' if current in completing else '此版本暂无歌词'))
        words.configure(state='disabled')
        show_cover()
    def choose(event=None):
        nonlocal current,options_touched
        ids=tree.selection()
        if not ids or current==int(ids[0]):
            return
        current=int(ids[0])
        options_touched=False
        row=rows[current]
        if not (row.cover and row.lyric) and current not in checked:
            checked.add(current)
            completing.add(current)
            threading.Thread(target=load_assets,args=(current,),daemon=True).start()
        display(True)
    def apply_filter(*unused):
        nonlocal current
        keywords=identity(filter_text.get()).split()
        visible=[str(i) for i,fields in enumerate(searchable) if all(k in fields for k in keywords)]
        # Detached tree items keep their original IDs, including for pending artwork downloads.
        for item in tree.selection():
            if item not in visible:
                tree.selection_remove(item)
        if current is not None and str(current) not in visible:
            current=None
            for key,check in checks.items():
                flags[key].set(False)
                check.configure(state='disabled')
            art.configure(image='',text='封面预览')
            words.configure(state='normal')
            words.delete('1.0','end')
            words.configure(state='disabled')
        tree.set_children('',*visible)
        count_label.configure(text=f'显示 {len(visible)} / {len(rows)} 条')
        clear_filter.configure(state='normal' if filter_text.get() else 'disabled')
        refresh_options()

    def focus_filter(event):
        filter_entry.focus_set()
        filter_entry.selection_range(0,'end')
        return 'break'
    def poll():
        nonlocal timer
        while not responses.empty():
            kind,idx,data,img=responses.get()
            if kind=='assets':
                completing.discard(idx)
                rows[idx]=data
                tree.set(str(idx),'data','、'.join(v for v,item in (('封面',data.cover),('歌词',data.lyric)) if item) or '—')
                if current==idx:
                    display(False)
            else:
                photos[idx]=(data,ImageTk.PhotoImage(img,master=top) if img else None)
                if current==idx:
                    show_cover()
        timer=top.after(100,poll)
    tree.bind('<<TreeviewSelect>>',choose)
    top.bind('<Control-f>',focus_filter)
    filter_trace=filter_text.trace_add('write',apply_filter)
    trace=term.trace_add('write',lambda *_:refresh_options())
    apply_filter()
    (tree if rows else entry).focus_set()
    timer=top.after(100,poll)
    parent.wait_window(top)
    term.trace_remove('write',trace)
    filter_text.trace_remove('write',filter_trace)
    # Release Tcl-backed resources on the event thread before the next search starts.
    photos.clear()
    flags.clear()
    term=mode=filter_text=None
    poll=None
    return selection[0] if selection else None
