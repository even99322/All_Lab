"""Safe placeholder for a future Time Domain program integration."""

from app.interfaces.base import BaseInterface, InterfaceContext, InterfaceResult


class TimeDomainInterface(BaseInterface):
    def is_available(self) -> bool:
        return False

    def launch(self, context: InterfaceContext) -> InterfaceResult:
        del context
        return InterfaceResult.unavailable()
