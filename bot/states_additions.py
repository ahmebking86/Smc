"""
إضافات الـ States الجديدة
أضف الأسطر دي داخل bot/states.py في الـ tuple.
"""

# أضف هذه الـ states في نهاية الـ range الموجود
# مثال: بعد WAIT_REPLACE_CONFIRM أضف:

WAIT_DELETE_CONFIRM = 12
WAIT_ADD_FUNDS_AMOUNT = 13
WAIT_ADD_FUNDS_CONFIRM = 14
WAIT_REDUCE_FUNDS_AMOUNT = 15
WAIT_REDUCE_FUNDS_CONFIRM = 16

# وعدّل الـ range ليصبح range(17) بدلاً من range(12)
