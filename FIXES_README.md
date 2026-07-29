# إصلاحات بوت إعادة التوازن (Smc)

## الملفات المعدّلة

### 1. `database/db.py` (استبدل الملف كاملاً)
- إصلاح schema جدول `bot_settings` تلقائياً عند اكتشاف الأعمدة القديمة
- إعادة اتصال تلقائية عند `connection already closed` أو SSL errors
- زيادة حجم pool الـ PostgreSQL إلى 15

### 2. `main.py` (استبدل الملف كاملاً)
- `post_shutdown` ينهي الـ monitor بشكل نظيف
- تجاهل أفضل لـ Telegram Conflict
- `drop_pending_updates=True` لمنع مشاكل عند إعادة التشغيل

### 3. `trading/bitget_client.py` (طبّق الـ PATCH)
- زيادة Connection Pool إلى 20 لمنع "Connection pool is full"
- Retry تلقائي على أخطاء 429/5xx
- timeout أطول قليلاً

## خطوات التطبيق على GitHub + Railway

1. استبدل الملفات:
   - `database/db.py` ← النسخة الجديدة
   - `main.py` ← النسخة الجديدة
   - طبّق تعديلات `bitget_client.py` حسب `bitget_client_PATCH.md`

2. احذف الملفات القديمة غير المستخدمة (اختياري لكن مُستحسن):
   - `trading/grid_engine.py`
   - `log_analysis.md` (يتحدث عن الشبكة القديمة)

3. Commit + Push:
```bash
git add database/db.py main.py trading/bitget_client.py
git commit -m "fix: robust DB schema, connection pool, clean shutdown"
git push
```

4. على Railway:
   - تأكد أن هناك **Service واحد فقط** للبوت (أوقف أي deployment قديم)
   - أعد Deploy
   - راقب اللوج: يجب أن ترى `✅ قاعدة البيانات جاهزة` بدون أخطاء `column "value"`

## ملاحظات إضافية

- إذا استمر Conflict: اذهب إلى Railway → Settings → وقم بـ Restart، أو احذف الخدمة القديمة.
- بعد أول تشغيل ناجح، أدخل مفاتيح API من جديد عبر البوت (لأن الجدول أُعيد إنشاؤه).
- الـ monitor interval = 30 ثانية (من config.py) — مناسب.

بعد التطبيق، أرسل لوج جديد لو ظهرت أخطاء أخرى.
