# QQ语音转文字 · v2.0.1

AstrBot 插件：收到 QQ 语音后，使用本地 Whisper 模型识别中文，并将语音消息替换为文字，供后续消息处理使用。

仓库：<https://github.com/Cheela-star/Cheela>

## 功能

- 使用 `silk-wasm` 解码 QQ SILK 语音，使用 Transformers.js 和 `Xenova/whisper-base` 在本地 CPU 上识别。
- 随包保留量化 ONNX 模型；识别时禁止远程下载模型，不需要语音识别 API Key。
- 自动启动本地识别服务，默认仅监听 `127.0.0.1:18321`。
- 支持启停、语音时长限制、文字前缀和识别失败提示。

## 安装

需要 AstrBot `>=4.14.0`、可用的 QQ 消息适配器，以及可运行 `worker/package.json` 中依赖的 Node.js 和 npm。下载语音需要机器人环境能够访问适配器提供的语音地址。

1. 将本仓库克隆或解压到 AstrBot 的 `data/plugins/astrbot_plugin_qq_voice_text`，确保该目录直接包含 `main.py` 和 `metadata.yaml`。也可在 AstrBot 插件管理中使用仓库地址安装。
2. 在插件目录中为 AstrBot 使用的 Python 环境安装依赖：

   ```sh
   python -m pip install -r requirements.txt
   ```

3. 进入插件的 `worker` 目录，安装 Node.js 依赖：

   ```sh
   npm install
   ```

   插件不会自动运行此命令。安装依赖需要联网；完成后语音识别在本地进行。

4. 确认模型目录为 `worker/models/Xenova/whisper-base/`，其中 `onnx/` 包含两个量化模型文件。
5. 重启或重载插件，在插件配置中填写需要的参数。Node.js 不在 PATH 中时，填写 `node_path` 的绝对路径。

如果 AstrBot 运行在容器中，Node.js、依赖和模型也须安装在同一容器中；宿主机的安装不能直接替代容器内环境。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 启用语音识别 |
| `worker_port` | `18321` | 本地识别服务端口 |
| `node_path` | 空 | Node.js 可执行文件路径；留空自动查找 |
| `model_dir` | 空 | 模型根目录；留空使用 `worker/models`。自定义目录下须保留 `Xenova/whisper-base/` 层级 |
| `max_seconds` | `120` | 最长语音时长（秒） |
| `prefix` | 空 | 识别文字前缀 |
| `fallback_notice` | `true` | 失败时将语音替换为失败提示 |
| `show_error_detail` | `false` | 在失败提示中显示错误详情 |

## 使用与限制

安装完成后发送 QQ 语音即可触发。插件修改传给后续处理流程的消息，不会额外主动发送一条转写结果；最终是否回复由 AstrBot 的后续处理决定。

当前 worker 按 SILK 格式解码，并固定使用中文识别。其他音频格式、其他语言或不同适配器的兼容性需要实际验证。模型推理使用 CPU，首次加载及较长语音可能耗时较多。

## 第三方材料

模型来自 [Xenova/whisper-base](https://huggingface.co/Xenova/whisper-base)，其模型页面标注 Apache-2.0；原始 Whisper 项目见 [openai/whisper](https://github.com/openai/whisper)，采用 MIT 许可。第三方声明及许可证位于 `THIRD_PARTY_NOTICES.md` 和 `licenses/`。

本次发布保留原有插件功能代码，修正仓库地址，补充安装说明、Python 依赖声明和第三方许可说明，并排除运行日志。尚未完成真实 QQ 环境中的端到端测试。
