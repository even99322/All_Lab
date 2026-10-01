"""labcontrol 統一例外類別。前端只需要 catch LabControlError 就能處理所有框架錯誤。"""


class LabControlError(Exception):
    """框架內所有錯誤的基底類別。"""


class ConfigError(LabControlError):
    """設定檔格式錯誤、找不到儀器名稱、driver 未註冊等。"""


class InstrumentError(LabControlError):
    """儀器通訊或狀態錯誤。"""


class InstrumentTimeout(InstrumentError):
    """儀器在限定時間內沒有完成動作（例如 VNA *OPC 逾時）。"""


class LimitError(InstrumentError):
    """設定值超出軟體安全上下限。"""


class InstrumentBusy(InstrumentError):
    """儀器已被其他 owner（例如正在跑的量測）獨佔，目前不能寫入。"""


#: 舊名稱（v0.x 開發版），保留相容
LabMasterError = LabControlError
