"""Safe placeholder for a future measurement program integration."""

from app.interfaces.base import BaseInterface, InterfaceContext, InterfaceResult


class MeasurementInterface(BaseInterface):
    def is_available(self) -> bool:
        return False

    def launch(self, context: InterfaceContext) -> InterfaceResult:
        del context
        return InterfaceResult.unavailable()
