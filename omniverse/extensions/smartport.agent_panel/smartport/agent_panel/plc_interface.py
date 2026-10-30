"""Allowlisted local PLC service and cargo-physics actions; no velocity commands."""
from __future__ import annotations

import asyncio
import time


def control_plc_zmq(owner, args, load):
    manager = load("rtg_plc_service_manager").get_plc_service_manager()
    adapter = load("rtg_plc_scene_driver")

    def status():
        scene = adapter.get_rtg_plc_status()
        service = manager.status()
        service["managed_ready"] = service["ready"]
        service["ready"] = bool(service["ready"] or scene["connected"])
        service["externally_managed"] = bool(scene["connected"] and not service["managed_running"])
        return {"service": service, "adapter": scene,
                "ready_for_commands": scene["connected"] and scene["scene_ready"],
                "note": "启动只连接数字场景，控制合保持原状态；首次连接默认为关闭。"
                        "physics_on 会申请本地控制合并开启 ZMQ 箱体物理模式，不发送速度指令。"
                        "未打开 RTG 场景时不能提供位置数据。"}

    action = args["action"]
    if action == "status":
        return status()
    if action == "disconnect":
        adapter.stop_rtg_plc_driver()
        return status()
    if action in {"physics_on", "physics_off"}:
        adapter.set_rtg_plc_physics(action == "physics_on")
        return status()

    async def connected_within(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if adapter.get_rtg_plc_status()["connected"]:
                await asyncio.sleep(.05)  # process welcome/config on Kit's next update
                return True
            await asyncio.sleep(.05)
        return False

    async def run():
        if action == "stop":
            # Disarm on the Kit thread before terminating the owned network process.
            adapter.stop_rtg_plc_driver()
            await manager.stop()
            return status()
        service_started = adapter_started = False
        try:
            port = (manager.ports if manager.status()["ready"] else manager.config()).get("scene_port", 48720)
            scene = adapter.get_rtg_plc_status()
            if scene["running"]:
                if scene["scene_port"] != port:
                    raise RuntimeError("Kit 适配器连接的是另一个端口，请先 disconnect 再连接。")
            else:
                adapter.start_rtg_plc_driver(port=port)
                adapter_started = True
            # A compatible service may already have been started in a terminal
            # or by an earlier Kit session. Its welcome identifies the protocol;
            # connecting first avoids spawning a duplicate that cannot bind.
            if action == "start" and not await connected_within(.4):
                try:
                    service_started = (await manager.start())["started"]
                except RuntimeError:
                    if not await connected_within(.4):
                        raise
            if await connected_within(4):
                return status()
            raise RuntimeError("Kit 适配器未连接到 ZMQ 服务。请检查服务是否已启动及 scene_port 配置；"
                               + str(adapter.get_rtg_plc_status().get("error")))
        except BaseException:
            if adapter_started:
                adapter.stop_rtg_plc_driver()
            if service_started:
                await manager.stop()
            raise

    return owner.job("plc_zmq_" + action, run())
