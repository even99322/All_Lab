"""Safe placeholder for a future Lab LaTeX program integration."""

from app.interfaces.base import BaseInterface, InterfaceContext, InterfaceResult


class LabLatexInterface(BaseInterface):
    def is_available(self) -> bool:
        return False

    def launch(self, context: InterfaceContext) -> InterfaceResult:
        del context
        return InterfaceResult.unavailable()
