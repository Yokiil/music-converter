# bin —— 放 FFmpeg 的位置

把这两个文件放在本目录下，程序会优先使用它们：

```
bin/
├─ ffmpeg.exe
└─ ffprobe.exe
```

没有的话，程序会继续在系统 `PATH` 中查找，同样可以工作。

## 从哪里获取

Windows 推荐 [Gyan FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) 的 `ffmpeg-release-essentials.zip`：

1. 下载并解压；
2. 从解压出的 `bin` 目录里复制 `ffmpeg.exe` 与 `ffprobe.exe`；
3. 粘贴到本目录。

两个文件合计约 210 MB，因此**不随本仓库分发**，也不在 Release 压缩包里。
本目录除了这个说明文件之外的内容都会被 Git 忽略。
