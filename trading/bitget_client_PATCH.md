# إصلاحات bitget_client.py

## 1. زيادة حجم Connection Pool ومنع "Connection pool is full"

في بداية الكلاس `BitGetClient.__init__` استبدل إنشاء الـ session بهذا:

```python
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class BitGetClient:
    def __init__(self):
        self.base = BITGET_BASE_URL
        self.session = requests.Session()

        # === FIX: larger pool + retries ===
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=retry_strategy,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # =================================

        self._precision_cache: dict[str, tuple[int, int]] = {}
        self._min_notional_cache: dict[str, float] = {}
        self._min_base_cache: dict[str, float] = {}
```

## 2. زيادة timeout قليلاً وتقليل الضغط

في دوال `_get` و `_post` غيّر `timeout=10` إلى `timeout=15`.

## 3. إضافة تأخير بسيط عند الطلبات المتزامنة (اختياري لكن مفيد)

في `rebalance_engine.py` الـ `time.sleep(0.3)` و `0.4` موجودة بالفعل — حافظ عليها.

## 4. تأكد أن `get_bitget()` يرجع نفس الـ instance (singleton)

في نهاية الملف يجب أن يكون:

```python
_client: BitGetClient | None = None

def get_bitget() -> BitGetClient:
    global _client
    if _client is None:
        _client = BitGetClient()
    return _client
```

---

بعد التعديل أعد Deploy على Railway.
