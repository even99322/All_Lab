"""範例 hook：自動找電流上下界。

掃描時追蹤 |S21| 最深點，當它碰到 VNA 視窗左/右邊界時記錄當下電流並停止量測。
往上掃一次得到一個邊界，把 start/stop 對調再掃一次得到另一個（見 config/experiments/find_bounds.yaml）。

結果寫在：record.notes["edge"]、ctx.shared["bounds"]，並以 run.hook 事件通知前端。
"""
import numpy as np

from labcontrol.core import register_hook
from labcontrol.measure.hooks import Hook, HookResult


@register_hook("dip_edge_stop")
class DipEdgeStop(Hook):
    """config: channel (S21)、edge_points (3)、min_depth_db (10：比中位數深多少才算吸收峰)"""

    def after_point(self, ctx, record):
        if not self.enabled:
            return None
        tr = np.asarray(record.data[self.config.get("channel", "S21")])
        mag = 20 * np.log10(np.abs(tr) + 1e-30)
        k = int(np.argmin(mag))
        if np.median(mag) - mag[k] < float(self.config.get("min_depth_db", 10)):
            return None
        edge = int(self.config.get("edge_points", 3))
        side = "left" if k < edge else "right" if k >= len(mag) - edge else None
        if side is None:
            return None
        record.notes["edge"] = side
        ctx.shared.setdefault("bounds", {})[side] = dict(record.setpoints)
        sp = ", ".join(f"{a.name}={record.setpoints[a.name] * a.display_scale:.6f} {a.display_unit}"
                       for a in ctx.dataset.axes)
        return HookResult("stop", f"最深點觸及{'左' if side == 'left' else '右'}界：{sp}")
