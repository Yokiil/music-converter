# 音乐转换器

Windows 本地音频工具：先辨认文件，再导出原编码或批量转成 MP3、FLAC、M4A/AAC、WAV、Ogg/Vorbis。转换不需要联网或登录音乐平台，不覆盖原文件和已有输出。

**普通音频可直接转换；网页版已内置常见 NCM 封装的公开格式参数，无需另传 JSON。** 其他平台的部分封装仍需服务器配置匹配版本的导出参数，支持范围见下表。内置值不是账号信息或每首歌的密钥，来源和许可见 [第三方声明](./THIRD_PARTY.md)。不处理未知封装时，不会仅改后缀来充当成功。

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

| 平台 | 已用合成文件验证的结构 | JSON 参数组 |
|---|---|---|
| 网易云音乐 | NCM；验证过内层 MP3、FLAC、封面及中文标签 | 网页后端默认内置；桌面端或自定义覆盖可用 `ncm.core_key`（16 字节）与 `tag_key`（16 字节） |
| QQ 音乐 | QMC v1（`.qmc*` 老封装）；QMC v2 的长度尾、QTag、STag，并包含 EncV2 合成用例。`.mflac`、`.mgg` 等后缀不等于兼容 | `qmc1`：`mask`（44/128/256 字节）；`qmc2`：`core_key`（8 字节），EncV2 还需有序的 `garble_keys`（每项 16 字节）；STag 需要与该文件对应的 `master_key`（128/256/512 字节） |
| 酷狗音乐 | KGM **v3**、VPR **v3**；新版 KGM/KGMA 未验证 | `kgm`：`table1`、`table2`、`tablev2`（各 272 字节），VPR 另需 `vpr_key`（17 字节） |
| 酷我音乐 | `yeelion-kuwo` 头的 KWM 结构 | `kwm`：`core_key`（32 字节） |

参数字段的值用十六进制字符串（不带 `0x` 或空格），`garble_keys` 用字符串数组。顶层可只包含需要的一组，例如 `{"ncm":{"core_key":"你的16字节参数的32位十六进制文本"}}`（占位文本并非有效参数）。`master_key` 会覆盖该次会话中 QQ v2 文件的内嵌密钥，只在单独处理匹配文件时加载该组。不要把含参数的文件提交到 Git，也不要分享个人账号或凭据。

封装上限为每个文件 256 MiB。QQ v2 无受支持文件尾的变体、NCM 以外的网易云封装、KGM 非 v3、账号授权或设备绑定的缓存，以及没有列出的平台或格式，**不在当前版本承诺范围**。`.qmc*` v1 仅是按后缀得到的候选，必须通过音频验证才会发布输出。请只处理你有权导出的文件；能在平台内离线听，不等于取得授权导出。

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
- 本仓库仅存放项目源码、文档和合成测试代码。第三方依赖、执行文件、本地参数、歌曲与生成输出不提交。此私有仓库未授予额外的开源再分发许可。
