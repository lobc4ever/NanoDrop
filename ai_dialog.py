import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from credentials import DEFAULT_MODEL, DEFAULT_URL, clear_settings, save_settings, settings


class AISettingsDialog(simpledialog.Dialog):
    def __init__(self, parent, profile):
        self.profile = profile
        super().__init__(parent, '搜索 AI 设置')

    def body(self, parent):
        self.address = tk.StringVar(self, self.profile.base_url)
        self.model = tk.StringVar(self, self.profile.model)
        self.secret = tk.StringVar(self)
        status = '已配置 · ' + self.profile.provider if self.profile.key else '未配置 · 使用本地候选排序'
        ttk.Label(parent, text=status).grid(row=0, column=0, columnspan=2, sticky='w', pady=6)
        for row, (title, var) in enumerate((('API 基础地址', self.address), ('模型', self.model),
                                            ('API Key', self.secret)), 1):
            ttk.Label(parent, text=title).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            entry = ttk.Entry(parent, textvariable=var, width=42, show='*' if row == 3 else '')
            entry.grid(row=row, column=1, sticky='ew', pady=4)
        hint = '密钥留空会沿用当前地址的密钥。' if self.profile.key else '输入自己的密钥即可启用 AI 辅助匹配。'
        ttk.Label(parent, text=hint + '\n设置仅保存在当前 Windows 账户的凭据管理器。'
                  '\n兼容服务需支持 Chat Completions。').grid(row=4, columnspan=2, sticky='w', pady=7)
        ttk.Button(parent, text='使用 DeepSeek 默认地址与模型', command=self.defaults).grid(row=5, columnspan=2, sticky='w')
        return entry

    def defaults(self):
        self.address.set(DEFAULT_URL)
        self.model.set(DEFAULT_MODEL)

    def buttonbox(self):
        box = ttk.Frame(self, padding=8)
        ttk.Button(box, text='清除配置', command=self.clear,
                   state='normal' if self.profile.key else 'disabled').pack(side='left')
        ttk.Button(box, text='取消', command=self.cancel).pack(side='right', padx=4)
        ttk.Button(box, text='保存', command=self.ok).pack(side='right')
        box.pack(fill='x')
        self.bind('<Return>', self.ok)
        self.bind('<Escape>', self.cancel)

    def validate(self):
        try:
            profile = settings(self.address.get(), self.model.get(), self.secret.get(), self.profile)
            save_settings(profile)
        except (ValueError, OSError) as exc:
            messagebox.showerror('设置未保存', str(exc), parent=self)
            return False
        self.result = '已保存搜索 AI 设置。'
        return True

    def clear(self):
        if not messagebox.askyesno('清除搜索 AI 配置', '删除此账户保存的搜索 AI 密钥和服务设置？', parent=self):
            return
        try:
            clear_settings()
        except OSError as exc:
            messagebox.showerror('清除失败', str(exc), parent=self)
            return
        self.result = '已清除搜索 AI 配置，候选将使用本地排序。'
        self.cancel()
