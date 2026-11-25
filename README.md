# NanoDrop

在 Windows 上管理第三代 iPod nano。连接设备后，把本地音乐拖入窗口即可导入，运行成品无需安装 Apple Music、iTunes 或 Python。

## 使用

1. 从 [Releases](../../releases/latest) 下载 Windows x64 版 `NanoDrop.exe`。
2. 通过 USB 连接第三代 iPod nano，等待窗口显示设备曲库。选择「添加歌曲」或把音频拖进待导入列表，再点击「导入到 iPod」。
3. 在「设备曲库」中查看歌曲大小，修改曲名、艺术家、专辑、歌词和封面，或多选歌曲后删除。操作完成后通过 Windows 安全弹出设备。

## 联网匹配与 API Key

在「工具 → 搜索 AI 设置（API Key）」输入自己的 DeepSeek 密钥并保存。使用其他兼容 Chat Completions 的服务时，填写该服务的 HTTPS 基础地址（按需含 `/v1`）、模型名称和对应密钥。此窗口也可以更换密钥或清除配置。配置保存在当前 Windows 用户的凭据管理器中。

按曲名或歌手检索后，可以用关键词筛选候选，再逐项确认要写入的资料。音乐目录接收检索词。启用 AI 辅助时，所选服务还会收到曲名、歌手、专辑、时长、检索词和候选资料，用于模糊排序和建议检索词。留空配置也能联网查询音乐目录并在本地排序。

## 从源码构建

在 Windows x64 上安装带 Tcl/Tk 的 Python 3.12，在仓库根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm NanoDrop.spec
```

可执行文件生成在 `dist\NanoDrop.exe`。导入、歌词、封面、故障恢复和设置窗口的验证步骤见[使用与构建](使用与构建.txt)。

NanoDrop 采用 [GPL-3.0-or-later](LICENSE)。iPod 曲库引擎来自 iOpenPod，随附组件的来源和许可证见 [licenses](licenses/)。
