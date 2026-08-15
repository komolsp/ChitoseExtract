# ChitoseExtract v1.0

面向 **DLsite 同人音声** 的 Windows 桌面批处理工具。
---
图形化主程序和配置界面。

主要基于[prekikoeru](https://github.com/Sakyoriii/prekikoeru)和[dlsite-doujin-renamer](https://github.com/yodhcn/dlsite-doujin-renamer)进行扩展功能，修复bug和提升易用性。

主要开发方式为鞭打ai

---

## 功能概览

| 步骤 | 说明 |
|------|------|
| **解压** | 常见压缩包格式改后缀、伪装扩展名、嵌套压缩包、隐写压缩包、分卷压缩等一键通杀，密码库多进程跑字典秒碰撞出密码，自动删除层层压缩套娃文件夹 |
| **归档** | 识别RJ号匹配音声作品放入音声库，将未识别 RJ 号的作品移入资源库，或整理到指定工作目录 |
| **过滤** | 按正则规则删除无 SE 版、MP3 冗余、宣传文件等 |
| **重命名** | 根据 RJ 号从 DLsite 拉取元数据，按模板重命名文件夹，将文件夹封面改为作品封面 |
| **转 FLAC** | 将 WAV 转为 FLAC 保持高音质同时减少磁盘占用 |
| **写入元数据** | 写入标签与封面，便于本地播放器识别 |

所有功能都可以自选是否在流程中启用，也可单独使用任一功能

## 程序界面预览
<img width="1456" height="1042" alt="image" src="https://github.com/user-attachments/assets/ab1230e5-ca8d-4fcc-85be-1913d8f053ca" />

其他特性：
- 解压时显示磁盘读写速度，实时监控任务进行情况
- 拖放文件/文件夹到窗口即可加入任务队列
- 逻辑删除（回收站）与套娃解压中间层清理
- 多进程并行解压，可配置 7-Zip 内部线程数
- 设置对话框 + `config.yaml` 双轨配置

---

## 系统要求

- **操作系统**：Windows 10 / 11（64 位）
- **网络**：重命名、写入元数据步骤需访问 DLsite（可配置 HTTP 代理）
- **磁盘**：建议将输出目录设在 SSD；大批量并行解压时注意磁盘负载


---

## 从源码运行

### 环境

- Python **3.9+**（推荐 3.11 / 3.13）
- 已安装 [7-Zip](https://www.7-zip.org/)（打包脚本会自动复制到 `7zip/`）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动

```bash
python main.py
```

或使用无控制台启动脚本：

```bash
launch.bat
```

启动失败时请查看同目录下的 `startup_error.log`。

---

## 目录结构

```
源码/
├── main.py              # 程序入口
├── gui.py               # 图形界面
├── task_runner.py       # 任务调度与流水线
├── unzipper.py          # 解压核心逻辑
├── config.yaml          # 主配置文件
├── password.txt         # 解压密码库
├── build.py             # PyInstaller 打包脚本
├── build.bat            # 一键打包（Windows）
├── dist/                # 打包输出目录
│   └── ChitoseExtract.exe
├── 7zip/                # 内置 7-Zip（开发与打包共用）
├── flac/                # 内置 flac
├── ffmpeg-minimal/      # 内置 minimal ffmpeg
├── dlrenamer/           # DLsite 元数据重命名模块
├── scraper/             # DLsite 刮削器
├── volume/              # 分卷识别与解析
└── tests/               # 自动化测试（188 项）
```

---

## 配置说明

主配置文件为 exe 旁的 `config.yaml`。也可在程序内点击「设置」修改常用项，或「打开 config.yaml」编辑高级选项。

### 必填路径

```yaml
path:
  output: D:/音声库          # 解压输出目录（必填）
  recycle: D:/回收站         # 逻辑删除回收站（必填）
  resource: D:/资源库        # 未识别 RJ 的作品移入此处（可留空）
```

> **路径写法注意**：请使用正斜杠 `D:/文件夹/子目录`，或双反斜杠 `D:\\文件夹\\子目录`。
> 不要在双引号内写单反斜杠（如 `"D:\测试"`），YAML 会将其解析为非法转义而报错。

### 流水线步骤

在 `workflow_steps` 或设置界面的「工作流」中勾选参与自动后续的步骤：

```yaml
workflow_steps:
  unzip: true           # 解压
  archive: true         # 归档
  filter: true          # 过滤
  rename: true          # 重命名
  convert_audio: false  # 转 FLAC
  tag_audio: false      # 写入元数据
```

`auto_next: true` 时，从侧栏选中的起始步骤起，会按顺序自动执行后续已勾选的步骤。

### 解压密码

密码单独存放在 `password.txt`，每行一个，与 `config.yaml` 分开管理，便于维护。

### 重命名模板

在 `renamer` 段配置 DLsite 刮削与命名模板，常用占位符：

| 占位符 | 含义 |
|--------|------|
| `rjcode` | RJ 号 |
| `work_name` | 作品名 |
| `maker_name` | 社团名 |
| `cv_list_str` | 声优列表 |
| `release_date` | 发售日 |

修改 `scraper_locale` 后，建议删除 `dlrenamer/cache.db` 以刷新元数据缓存。

更详细的注释见 `config.yaml` 文件内说明。


---

## 运行测试

```bash
pip install -r requirements.txt
python -W ignore::ResourceWarning -m unittest discover -s tests -q
```

当前测试套件共 **276** 项，覆盖分卷解析、嵌套解压、密码处理、过滤规则、音频转换等核心逻辑。

---

## 常见问题

### 配置文件解析失败

日志出现 `ScannerError: found unknown escape character` 时，通常是 `config.yaml` 中路径使用了非法转义。检查 `path` 段，将反斜杠改为正斜杠：

```yaml
# 错误
output: "D:\音声\目录"

# 正确
output: D:/音声/目录
```

### 配置缺少关键项

`path.output` 与 `path.recycle` 为必填项。启动前请在设置中填写，或直接编辑 `config.yaml`。

### 重命名失败 / 元数据拉取慢

- 检查网络与 `renamer.scraper_http_proxy` 代理设置
- 适当增大 `scraper_read_timeout`
- 勿将 `scraper_sleep_interval` 设得过小，避免请求过快

### 日志文件被占用

多开实例时 `log.txt` 轮转可能报 `PermissionError`。建议只运行一个实例，或关闭占用日志的程序。

### float WAV 转 FLAC 失败

确认 `ffmpeg-minimal/ffmpeg.exe` 存在，或在 `audio_convert.ffmpeg_fallback_path` 中指定 ffmpeg 路径。

---

## 安全建议

- 建议保持 `logical_deletion: true`，过滤与删除的文件先进入回收站
- 建议 `del_after_unzip: false`，首次解压失败时不删除原始压缩包
- 确认输出无误后，再手动清理 `recycle` 目录与原始压缩包

---

## 版本信息

- **程序名称**：ChitoseExtract
- **当前版本**：v1.0
- **平台**：Windows

---

## 参考文献
- [prekikoeru](https://github.com/Sakyoriii/prekikoeru)
- [dlsite-doujin-renamer](https://github.com/yodhcn/dlsite-doujin-renamer)
- [SteganographierGUI](https://github.com/cenglin123/SteganographierGUI)
- [dlonsei-formatter](https://github.com/somebelly/dlonsei-formatter)
