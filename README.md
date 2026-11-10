# 爆款复刻工作台

把一条带货爆款视频，换成你自己的模特和商品，复刻出一条新视频。整个过程在一个本地网页里完成：哪一步都看得见，哪一格不对就改哪一格，调用付费接口之前都会先让你确认。

```
01 上传对标视频 → 02 拆解对标视频 → 03 选角和商品 → 04 对照和修改 → 05 成片
```

- **拆解在本地跑**：识别台词（faster-whisper / 可选 WhisperX）、切镜头、抽关键帧都在你自己电脑上完成；只有“理解剧情”这一步调用推理模型。
- **先出图，再出视频**：原片分镜和新分镜一格对一格，点一格写一句话只改这一格，拖框可以一次改几格。分镜满意了再生成视频。
- **批量裂变**：把做好的一条当母版，人物选几张、商品选几张，自动组合出一批（一次最多 20 条），有批量看板看每条的进度。
- **数据都在本地**：原片、素材、生成结果都存在本文件夹的 `projects/`；Key 只存在本文件夹的 `console.env`。

## 三步用起来

**1. 下载**：点本页右上角绿色的 **Code → Download ZIP**，解压到任意文件夹；会用 git 的话也可以：

```bash
git clone https://github.com/cs6h2wsz4r-ship-it/viral-replica-workbench.git
```

**2. 双击启动**：macOS 双击 `start.command`，Windows 双击 `start.bat`。

第一次启动会自动安装（约 10 分钟，只装一次）：Python 3.12、依赖、FFmpeg，以及拆解用的本地模型 Whisper medium（约 1.4GB）。全部装进本文件夹的 `runtime/`，不写到系统目录；连不上 Hugging Face 时会自动换国内镜像。装好后浏览器会自动打开 http://127.0.0.1:8765 ，以后再双击就是秒开。

**3. 填 Key**：网页右上角“设置”里填你自己的 Key（见下一节），就能开始复刻。

> macOS 第一次双击如果提示“无法打开，来自身份不明的开发者”：右键 `start.command` → 打开 → 再点“打开”。
> 想手动安装也可以：macOS 终端运行 `./install.sh`，Windows 双击 `install.bat`。国内网络加 `--mirror`（Windows 用 `-Mirror`）；要更准的逐词对齐加 `--whisperx`（Windows 用 `-WhisperX`，另需约 2GB）。

**卸载**：删除整个文件夹即可。

## 设置（网页右上角“设置”）

| 模型 | 用途 | 可选 |
| --- | --- | --- |
| 推理模型 | 拆解、写分段方案、改台词、检查 | 官方 OpenAI Key，或三方渠道（OpenAI 兼容接口、豆包、DeepSeek、自定义） |
| 生图模型 | 生成新分镜、按格改图、批量裂变 | gpt-image-2 自动画质 / 高画质、gpt-image-2.6 sunburst 高画质、三方渠道 |
| 生视频模型 | 分段生成视频 | 三方渠道（Seedance 2.0） |

Key 填你自己的，只保存在本文件夹的 `console.env`，不会写进项目文件，也不在 git 里（见 `.gitignore`）。

## 生视频（三方渠道 ViralTok）

生视频走三方渠道 ViralTok，在设置里填三方渠道 Key 即可，所需脚本已内置。

## 致谢和许可

拆解流程（媒体探测、逐词转录、镜头切点、带台词的帧网格）参考了 [Hypit](https://github.com/hypit-ai/hypit) 的做法，`console/kits.py` 中的 Seedance / GPT Image 提示词模板文字取自 Hypit。按 Hypit 许可证的要求，本项目以同样的许可证发布，详见 `LICENSE` 和 `THIRD_PARTY_NOTICES.md`：可以自用、改、再发布，但不能拿去收费分发或做成多租户服务。
