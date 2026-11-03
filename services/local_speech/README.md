# 本地语音服务（CPU）

本服务独立于 Kit，只监听 `127.0.0.1:8022`。已部署 Python 3.11、sherpa-onnx 2.0.1（CPU）、SenseVoice int8 多语言识别及 MeloTTS 中英混合语音合成。每次只处理一个推理请求，默认2个CPU线程，忙时返回503。

## 启动和停止

- 首次安装或重新部署：根目录 `setup_local_speech.cmd`
- 启动：`start_local_speech.cmd`（后台运行，不打开控制台窗口）
- 停止：`stop_local_speech.cmd`
- 查询状态：`.cache/local-speech/venv/Scripts/python.exe services/local_speech/manage.py status`
- 运行完整调用测试：`.cache/local-speech/venv/Scripts/python.exe services/local_speech/smoke_test.py`

环境、模型、日志、进程记录在 `.cache/local-speech/`；音频样本和测试结果在 `outputs/`。这些目录已被Git忽略，不提交模型权重和录音。模型下载地址和下载文件SHA256记录在 `models/*-receipt.json`；SHA256用于记录本次文件，不等同于上游签名验证。

## 本机 HTTP 接口

`GET /health`：模型就绪状态、PID、CPU provider、线程数、采样率。

`POST /tts`：`Content-Type: application/json`，请求 `{"text":"你好，欢迎使用智慧港口。","speed":1.0}`，返回单声道PCM16 WAV（44100Hz）。最多500字符，速度0.7–1.5。

`POST /asr`：`Content-Type: audio/wav`，请求体为WAV原始字节，返回 `text`、音频时长、推理耗时。接受最长60秒、单/双声道、8–48kHz WAV，模型内部重采样。请求体最多12MiB。服务不保存用户录音。

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8022/health
```

已接入8011管理员网页：语音输入、取消录音、上传WAV识别、朗读最新回答、自动朗读开关及停止朗读。录音最长60秒，识别结果填入输入框，校对后点击Send才发送给Agent；保持原有场景操作确认。

`/api/speech/health`、`/api/speech/asr`、`/api/speech/tts`由网页登录会话保护，仅管理员可调用，观众返回403；由8011代理请求到本机8022，不开放8022到局域网。自动朗读默认关闭，开启后只读新到达的Agent最终回答，不读历史欢迎语、工具调用或工具结果，退出页面停止麦克风与播放。回答超过一段时分段合成；可随时停止。WebRTC启动脚本会尝试启动已部署的语音服务。

本机localhost/127.0.0.1可以申请麦克风权限；其他电脑通过HTTP局域网IP访问时，麦克风受浏览器安全上下文限制，须使用可信HTTPS才能实时录音，也可上传WAV文件识别。此更改未配置局域网HTTPS或证书。

## 本机验证

2026-11-02，在运行Kit的情况下，约4.9秒合成语音耗时1.409秒，识别耗时0.163秒；进程工作集约658MB。此为合成音频回环测试，不代表真实麦克风、环境噪声或多人并发性能。

识别存在误差：测试中“岸桥”曾识别成“安桥”，“请查询”曾出现同音替换。尚未做港口词汇适配，后续应允许用户校对识别文字，再发送Agent。未宣称逐字准确或已经实现网页语音交互。

模型和运行库来源：

- https://github.com/k2-fsa/sherpa-onnx
- https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
- https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html

使用模型时遵循各模型及其上游训练数据的适用许可。本服务不包含声音克隆或自定义训练。
