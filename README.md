# 音乐转换器

Windows 本地音频工具：先辨认文件，再导出原编码或批量转成 MP3、FLAC、M4A/AAC、WAV、Ogg/Vorbis。转换不需要联网或登录音乐平台，不覆盖原文件和已有输出。

**网页版已默认接通普通音频，以及网易云、QQ 音乐、酷我、酷狗的下列可自动读取格式，无需客户填写参数或平台。** 这些默认值来自可核对的公开实现，不是用户账号信息。具体版本边界见下表；源码和许可见 [第三方声明](./THIRD_PARTY.md)。没有内嵌可恢复密钥的新封装或未知版本会明确失败，不会仅改后缀充当成功。

## 网页版

已提供中文网页与配套 HTTP API。运行 [Start-Backend.bat](./Start-Backend.bat) 后打开 [本机音乐转换网页](http://127.0.0.1:8787/)，即可拖入文件自动识别、选择格式、顺序批量转换并下载，多个结果可以打包成 ZIP。网页适配电脑和手机宽度，不需要另装前端开发工具。

服务器启动时读取一次固定配置，客户不需要填写平台或密钥。安装、配置、接口契约和浏览器测试方法见 [后端接入](./后端接入.md)。

## 桌面版开始使用

1. 安装带 Tkinter 的 Python 3.10 或更新版本，以及 [FFmpeg](https://ffmpeg.org/download.html) 和 ffprobe。在 Windows 上可从 [Gyan FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) 获取 essentials 构建；将 `ffmpeg.exe`、`ffprobe.exe` 放进项目的 `bin` 目录，或加入 `PATH`。
2. 双击 [Start.bat](./Start.bat)，或者运行 `python app.py`。应用已在这台电脑配置好时，可直接启动。
3. 添加、拖入文件或添加文件夹。双击诊断结果可看原因。普通文件如果包含多个音轨，使用第一条。
4. 默认选 **原始音频（不转码）**，保留兼容音轨的原编码。一定要 MP3 时选 **MP3**；码率选「原样 / 默认」时，源编码相同就不重新压缩，其他情况用默认码率。选择具体码率则会重新编码。
5. 选择保存目录，点「开始转换」。同名会加 `(1)`、`(2)`，成功后可点「打开输出」。

### 处理平台封装

先双击 [Install-Adapters.bat](./Install-Adapters.bat)（只写项目里的 `.deps`），或执行：

```powershell
python -m pip install --require-hashes --target .deps -r requirements-adapters.txt
```

用「加载本地参数…」选择你自己的 JSON 文件。参数只在当前应用会话生效，不上传、不自动保存。应用会重新诊断已添加的文件。「可尝试导出」表示已识别结构且参数字段齐全；参数正确与否要到实际导出时才会校验。「缺导出参数」或「暂不支持」的行会跳过。无需添加账号、Cookie、客户端路径或在线链接。

| 平台 | 网页默认支持（已用合成音频端到端验证） | 仍不支持自动转换的情况 |
|---|---|---|
| 网易云音乐 | 常见 NCM，内层 MP3/FLAC，中文标签、PNG/JPEG 封面 | 不是 NCM 的其他网易云缓存、未知变体或损坏文件 |
| QQ 音乐 | QMC v1；QMC v2 的长度尾、QTag、EncV2，含 Map 和 RC4 内层。`.mflac0`、`.mgg0` 是否可读取决于结构 | STag、musicex、缺少可恢复内嵌单曲密钥的版本 |
| 酷狗音乐 | KGM / KGMA / VPR 的 **type 2、type 3，slot 1**；读取并校验文件签名，无需三表 | type 4 或未知 type、未知 slot、签名不匹配 |
| 酷我音乐 | KWM，固定文件头与 1024 字节音频起点；自动试 32/64 位种子并验证实际音频 | 未知头、其他封装或解码后不是有效音频 |

普通文件通过 ffprobe 识别；平台文件优先看文件头、QQ 文件尾或默认掩码还原后的音频头。保留原始文件名仍有助于诊断，但改变后缀不能让未支持的版本变为可转换。上传后判定为可尝试导出，还会经过实际音轨校验；原编码平台输出也会进行完整解码检查。

桌面版或自定义变体仍可显式加载 JSON：`ncm` 含 `core_key` / `tag_key`（各 16 字节）；`qmc1.mask` 为 44/128/256 字节；`qmc2.core_key` 为 8 字节、`garble_keys` 为有序的 16 字节列表；`kwm.core_key` 为 32 字节；原生 `kgm.slot_key` 为 4 字节。旧版三表的显式配置保留为兼容路径（每表 272 字节，VPR 另需 17 字节参数），不影响网页默认的原生读取路径。

参数字段的值用十六进制字符串（不带 `0x` 或空格），`garble_keys` 用字符串数组。顶层可只包含需要的一组，例如 `{"ncm":{"core_key":"你的16字节参数的32位十六进制文本"}}`（占位文本并非有效参数）。`master_key` 会覆盖该次会话中 QQ v2 文件的内嵌密钥，只在单独处理匹配文件时加载该组。不要把含参数的文件提交到 Git，也不要分享个人账号或凭据。

封装读取核心每个文件最大 256 MiB，网页默认上传限制为 64 MiB，可由服务器调整。没有列出的平台或版本不在承诺范围。QQ 的单曲 `master_key` 只对应其匹配文件，服务器不会把它作为全站默认值。请只处理你有权导出的文件。

### 音质、标签与封面

- 「原始音频」在支持的 MP3、FLAC、AAC/ALAC、Ogg/Vorbis、Opus、部分 WAV/PCM 之间选择与音频编码对应的输出封装，只复制音频流。对其他编码可手动选择目标格式。它不复制视频画面，不保证容器逐字节相同。
- 标题、歌手、专辑及 JPEG/PNG 封面尽量保留到支持的目标（MP3、FLAC、M4A）；WAV、Ogg 在本工具中不写封面。标签能否保留还取决于源文件及目标格式。
- 把 MP3 转成 FLAC 不会恢复已经丢掉的音质。选择有损格式或较低位深时，可能改变音质；不会改动原文件。

## 本地验证

测试只生成单音调和纯色图，不带真实歌曲或平台参数。先装好 FFmpeg 和可选适配器，再在项目根目录运行：

```powershell
$env:REQUIRE_ADAPTERS = '1'
$env:TEST_GUI = '1'  # 有 Windows 桌面时启用窗口测试；无界面环境可不设置
python -m unittest -v tests.test_converter
```

想自己在窗口试用，可先生成可拖入的演示文件：

```powershell
python -m tests.fixtures
```

终端会打印本地样本目录和合成参数的位置。先加载 `demo-profile.local.json`，批量导入 `样本` 目录。STag 单独加载 `stag-profile.local.json` 再测试：它只匹配那一首合成文件。演示参数不能用于真实平台文件。

## 依赖和源码范围

- [libtakiyasha 2.1.1.post1](https://pypi.org/project/libtakiyasha/2.1.1.post1/) 是可选封装读取后端（[MIT](https://github.com/nukemiko/libtakiyasha/blob/2.1.1.post1/LICENSE)）。它的依赖 [pyaes](https://github.com/ricmoo/pyaes/blob/master/LICENSE.txt)（MIT）与 [Mutagen](https://pypi.org/project/mutagen/1.47.0/)（GPL-2.0-or-later）通过 pip 单独安装。
- [FFmpeg](https://ffmpeg.org/legal.html) 与 ffprobe 是独立进程，具体许可证随各自构建而定，不随此源码仓库分发。
- 原生酷狗／酷我读取模块以 LGPL-2.1-or-later 提供，对应源代码、修改说明和许可证随仓库保留。小型公开 QQ 密钥测试向量遵循其测试项目的 LGPL v3；详见 [第三方声明](./THIRD_PARTY.md)。
- 本仓库不提交真实歌曲、个人参数或第三方二进制。上述明确标注的第三方及移植模块按各自许可证分发，其余项目代码未授予额外的开源许可。
