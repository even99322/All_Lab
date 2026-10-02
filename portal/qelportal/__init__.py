"""QEL Lab 大程式伺服器（portal）：放在 NAS，所有模塊的入口與中繼。

* 帳號：直接用論文庫（paperlib）的帳號登入；站長＝論文庫的站長，可以開關每個人能用哪些模塊。
* 共用標籤：所有模塊同一批標籤，每個標籤可以連到論文。
* 數據登錄：量測完成的數據檔登錄在這裡（含量測設置與標籤），讀檔模塊與網頁都查得到。
* 中繼：量測指令與即時資料經這裡轉給 Lab Control Hub（使用者不需要 Hub token）。
* 模塊發佈：每個模塊各自上傳新版，桌面大程式各自更新。
* 網頁：電腦、Android、iPhone／iPad 都能用（可安裝成 App）。

只用 Python 標準函式庫；用官方 python 映像直接執行，不需要 build。
"""
__version__ = "1.0.1"
MODULE_ID = "portal"
PROTOCOL = 1
