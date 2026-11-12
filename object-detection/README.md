# 本机目标检测

当前使用 YOLO26s ONNX FP32，输入 640×640，在 CPU 上推理。EasyNVR 录像模板中的启用时段与人形、车辆、动物勾选决定是否写入对应事件。各路视频源和通道从数据库动态发现。模型、追踪和事件生成在本机运行。

- models/yolo26s.onnx 是生产模型，约 38 MB。models/yolox.onnx 是旧 YOLOX-S 回退模型。model-config.json 选择 yolo26s 或 yolox。
- 人形默认使用 [ByteTrack 追踪](tracking/README.md)，高阈值 0.5，低阈值 0.2 用于关联已有目标，短时漏检缓冲 5 秒。空闲目标采样 1 帧/秒，活动目标 2 帧/秒。
- 当同一位置的人物框和动物框明显重叠时，优先采用人物证据。模型报「熊」时额外要求局部同类复核，避免弯腰人物误报。已有事件未回填标签。
- status.json 提供各路取帧、识别类别、推理时间和错误，detector.log 记录进程问题。
- .venv 同时供智能录像和中继管理使用。删除整个环境后需要重建依赖才能启动。

## 重建运行环境

在本目录用 64 位 Python 3.12 执行：

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
& '.\.venv\Scripts\python.exe' -m unittest -v test_detector.py test_tracking.py
& '.\start.ps1'
```

检查 detector.py 中的 DB、RECORDS 和 FFMPEG 路径。若需要在 EasyNVR 原界面调整全部检测类别，使用与 7.7.421 匹配的 frontend/ 及其清单，核对 EasyNVR 的 WWWDir，升级原厂版本时先验证补丁兼容性。一般功能使用和迁移见 [主手册](../摄像头获取与事件预览-完整部署手册.md)。

切换模型后需重启检测进程。回退时核对 process.json 对应的是 detector.py，再停止并使用 start.ps1 启动。YOLO26s 升级前的代码备份在上级 backups/yolo26s-20261106-183606/，人物追踪升级前的代码见上级 maintenance/person-tracking-20261107/。需要重新从 .pt 导出 ONNX 时，按 export-requirements.txt 在单独环境运行 export_yolo26.py。日常 ONNX 推理使用 requirements.txt。
