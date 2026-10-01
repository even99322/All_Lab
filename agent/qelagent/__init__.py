"""QEL Lab 更新代理：NAS 上一直在執行的小容器，負責更新、備份、重建、重新啟動各服務。

服務自己停止時沒辦法回報進度，所以由代理執行，並把每一步即時回報給監控程式。
沿用 Lab Control Hub 控制代理的做法（Docker Engine API、只用標準函式庫），擴充成可以管理多個服務：
大程式網站（portal）、論文庫（paperlib）、量測中繼站（labhub），以及代理自己（agent）。
"""
__version__ = "1.0.0"
MODULE_ID = "agent"
DEFAULT_PORT = 8767
