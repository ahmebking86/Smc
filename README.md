# ميزة استبدال عملة — تعليمات التطبيق

## الملفات

| الملف | الإجراء |
|-------|---------|
| `bot/states.py` | استبدل الملف كاملاً |
| `bot/keyboards.py` | استبدل الملف كاملاً |
| `trading/rebalance_engine_REPLACE_METHOD.py` | انسخ الدالة والصقها داخل كلاس RebalanceEngine |
| `bot/handlers_REPLACE_ADDITIONS.py` | اتبع التعليمات داخله لإضافة الكود في handlers.py |

## خطوات

1. استبدل `bot/states.py` و `bot/keyboards.py` بالنسخ الجديدة.
2. افتح `trading/rebalance_engine.py`:
   - الصق دالة `replace_asset` بعد دالة `close_asset` وقبل قسم Load.
3. افتح `bot/handlers.py`:
   - عدّل الاستيرادات (أضف `replace_asset_kb` و الحالات الجديدة).
   - الصق الـ handlers الجديدة قبل `build_application`.
   - أضف `replace_conv` و الـ CallbackQueryHandler في `build_application`.
4. Commit + Push + Redeploy على Railway.

## طريقة الاستخدام

1. ادخل على محفظة نشطة
2. اضغط **🔁 استبدال عملة**
3. اختر العملة القديمة
4. اكتب العملة الجديدة (مثل XRP أو XRPUSDT)
5. أكد → البوت يبيع القديمة ويشتري الجديدة بنفس القيمة تقريباً
