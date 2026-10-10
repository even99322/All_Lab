"""
公式產生器（獨立執行）
    python formula_builder.py

輸入理想 S 參數模型 → 自動加上 Fano 相位與環境誤差 → 產生可給 main.py 載入的公式 .py
（在主程式中也可從「② 公式 → 公式產生器…」開啟，並直接載入產生的檔案）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    from PyQt6.QtWidgets import QApplication
    from fitapp.ui.formula_builder import FormulaBuilderDialog

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    dlg = FormulaBuilderDialog(start_dir=os.getcwd())
    dlg.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
