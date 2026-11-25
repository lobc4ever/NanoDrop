"""Per-user Windows Credential Manager storage."""
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass, field
import json
from urllib.parse import urlsplit

NAME = 'NanoDrop/SearchAI'
DEFAULT_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-flash'


@dataclass(frozen=True)
class SearchAI:
    base_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    key: str = field(default='', repr=False)

    @property
    def provider(self):
        return 'DeepSeek' if urlsplit(self.base_url).netloc.lower() == 'api.deepseek.com' else urlsplit(self.base_url).netloc


def settings(base_url, model, key, previous=None):
    base_url = base_url.strip().rstrip('/')
    model = model.strip()
    parts = urlsplit(base_url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or '?' in base_url or '#' in base_url or '\\' in base_url
            or not base_url.isascii() or not all(32 < ord(ch) < 127 for ch in base_url)):
        raise ValueError('请输入 HTTPS API 基础地址，可包含 /v1。')
    if parts.port == 0 or len(base_url) > 512:
        raise ValueError('API 地址无效或过长。')
    if not model or len(model) > 160 or not model.isprintable():
        raise ValueError('请输入服务商提供的模型名称。')
    key = key.strip()
    if not key and previous and previous.base_url == base_url:
        key = previous.key
    if not key or len(key) > 1024 or not all(32 < ord(ch) < 127 for ch in key):
        raise ValueError('请填写这个服务的 API Key。更换地址时需要重新输入。')
    return SearchAI(base_url, model, key)

class Credential(c.Structure):
    _fields_ = [('Flags',w.DWORD),('Type',w.DWORD),('TargetName',w.LPWSTR),('Comment',w.LPWSTR),
                ('LastWritten',w.FILETIME),('CredentialBlobSize',w.DWORD),('CredentialBlob',c.c_void_p),
                ('Persist',w.DWORD),('AttributeCount',w.DWORD),('Attributes',c.c_void_p),
                ('TargetAlias',w.LPWSTR),('UserName',w.LPWSTR)]

adv = c.WinDLL('Advapi32',use_last_error=True)
adv.CredReadW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(c.POINTER(Credential))]
adv.CredWriteW.argtypes = [c.POINTER(Credential),w.DWORD]
adv.CredFree.argtypes = [c.c_void_p]
adv.CredDeleteW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD]

def read_settings():
    entry = c.POINTER(Credential)()
    if not adv.CredReadW(NAME,1,0,c.byref(entry)):
        if c.get_last_error()==1168:
            return SearchAI()
        raise c.WinError(c.get_last_error())
    try:
        data=entry.contents
        record = json.loads(c.string_at(data.CredentialBlob,data.CredentialBlobSize))
        return settings(record['base_url'], record['model'], record['key'])
    finally:
        adv.CredFree(entry)

def clear_settings():
    if not adv.CredDeleteW(NAME,1,0) and c.get_last_error()!=1168:
        raise c.WinError(c.get_last_error())


def save_settings(profile):
    profile = settings(profile.base_url, profile.model, profile.key)
    raw = json.dumps(vars(profile), ensure_ascii=False).encode('utf-8')
    if len(raw) > 2560:
        raise ValueError('配置超过 Windows 凭据的长度限制。')
    buf=c.create_string_buffer(raw)
    cred=Credential(Type=1,TargetName=NAME,CredentialBlobSize=len(raw),CredentialBlob=c.addressof(buf),
                    Persist=2,UserName='NanoDrop')
    if not adv.CredWriteW(c.byref(cred),0):
        raise c.WinError(c.get_last_error())
