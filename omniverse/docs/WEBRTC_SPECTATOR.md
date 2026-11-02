# WebRTC 观众流

启动 `start_webrtc_headless_2k.cmd` 时同时启用主流与一条观众流，复用应用帧缓冲。

- 主流：TCP 49100 / UDP 47998，`http://服务器IP:8011/`
- 观众流：TCP 49200 / UDP 48000，`http://服务器IP:8011/?spectator=1`
- 当前画面固定 2560×1440；主流60fps，观众流目标30fps。
- 观众页面自动选择49200端口，并拦截场景鼠标键盘输入，不显示可操作的网页Agent输入框。画面中的原生Agent卡片仍是视频的一部分。
- 两个客户端观看同一视角；主流操作者切换视角后观众同步看到。不是每人独立相机。
- 一条观众流用于一个额外客户端。网络需允许HTTP8011、信令TCP49200及媒体UDP48000；主流另需49100/47998。
- 后端未增加身份认证或权限隔离，观众页面的输入屏蔽属于浏览器交互限制，不是访问控制。

依据 NVIDIA omni.kit.livestream.app 观众流配置：使用观众流时主流也需禁用dynamic resize。
https://docs.omniverse.nvidia.com/kit/docs/omni.kit.livestream.app/latest/Overview.html

更新：已扩展为3路观众流，TCP端口49200/49210/49220，UDP端口48000/48002/48004。观众登录后由会话自动分配空闲流。页面每30秒续租；离开页面主动释放，异常离线90秒后可重新分配。管理员网页使用HTML Agent输入/回复面板，原生面板从共享视频中隐藏，观众不接收Agent对话内容。
