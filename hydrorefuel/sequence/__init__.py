"""跨组件操作编排。

每个操作都是真实的跨模块调用链，顺序在编排里写死：卸氢按"软管连接 → 卸氢
阀开 → 卸氢 → 泄压放散 → 拔管"，增压充瓶按"目标瓶组核对 → 压缩机启动 →
升压 → 瓶组缓存"，加注按"队列确认 → 枪落座 → 预冷 → 开加注阀 → 压力阶梯与
流量核对 → 隔离停机 → 枪回位"，流量计在线校验按"被校表与参照路分段同步
走量 → 逐段偏差核对 → 出结论或作废"。顺序被破坏时返回 violations，由平台
写审计。
"""

from __future__ import annotations

from .fill import fill_vehicle
from .pressurize import pressurize_buffer
from .stop import stop_fill
from .unload import unload_truck
from .verify import verify_flow

__all__ = ["unload_truck", "pressurize_buffer", "fill_vehicle", "stop_fill", "verify_flow"]
