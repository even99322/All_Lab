"""模塊之間傳遞的動作名稱（本機傳遞與經大程式的事件共用）。

每個動作的 payload：

``open_file``     {"path": 本機路徑} 或 {"dataset_id": 數據編號}
                  → 讀檔模塊開啟這個數據檔。量測結束時量測模塊會送這個。
``apply_scheme``  {"path": 數據檔路徑} 或 {"scheme": 方案 dict} 或 {"dataset_id": 數據編號}
                  → 量測模塊把數據檔裡的量測設置載入流程圖。讀檔模塊拖動 / 選單會送這個。
``show_papers``   {"tags": [標籤…]}
                  → 顯示這些標籤對應的論文。
``ping``          {} → {"module": ..., "version": ...}
"""
from __future__ import annotations

OPEN_FILE = "open_file"
APPLY_SCHEME = "apply_scheme"
SHOW_PAPERS = "show_papers"
PING = "ping"

ALL = (OPEN_FILE, APPLY_SCHEME, SHOW_PAPERS, PING)

# 事件主題（經大程式廣播）
TOPIC_DATASET_CREATED = "dataset.created"
TOPIC_DATASET_UPDATED = "dataset.updated"
TOPIC_TAGS_CHANGED = "tags.changed"
TOPIC_HANDOFF = "handoff"            # 跨電腦傳給「同一個使用者」的某個模塊：{"module", "action", "payload"}
TOPIC_MODULES_CHANGED = "modules.changed"
