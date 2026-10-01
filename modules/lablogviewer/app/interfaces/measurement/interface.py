"""Measurement：把選取的數據交給 QEL Lab 量測模塊（Lab Control），套用它的量測設置。

只有從 QEL Lab 大程式開啟（有通信模塊 labcomm）時可用；否則維持原本的「尚未設定」。
"""

from app.interfaces import qel
from app.interfaces.base import BaseInterface, InterfaceContext, InterfaceResult


class MeasurementInterface(BaseInterface):
    def is_available(self) -> bool:
        return qel.AVAILABLE

    def launch(self, context: InterfaceContext) -> InterfaceResult:
        if not self.is_available():
            return InterfaceResult.unavailable()
        if context.selected_log_path is None:
            return InterfaceResult.failed("請先在 Browser 選一筆數據，再交給量測模塊套用它的量測設置。")
        try:
            qel.send_to_measurement(str(context.selected_log_path))
        except qel.CommError as e:
            return InterfaceResult.failed(str(e))
        return InterfaceResult.launched()
