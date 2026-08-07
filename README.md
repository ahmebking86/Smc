# Bitget Scalping Bot

بوت سكالبينج آلي لمنصة Bitget، مبني بلغة Python، جاهز للنشر على Railway.

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/%3Cusername%3E/%3Crepo-name%3E)
[![Open in GitHub](https://img.shields.io/badge/GitHub-Repo-181717?logo=github)](https://github.com/<username>/<repo-name>)

> ⚠️ استبدل `<username>` و `<repo-name>` في الرابطين فوق باسم حسابك واسم الريبو بعد ما ترفعه على GitHub، عشان زرار "Deploy on Railway" يشتغل صح.

## ⭐ تحكم كامل عبر تليجرام

البوت متصل بتليجرام وبيديك تحكم كامل وأنت في أي مكان:

| الأمر | الوظيفة |
|---|---|
| `/status` | حالة البوت (شغال/متوقف، Dry Run/حقيقي، % المخاطرة...) |
| `/balance` | الرصيد المتاح بالـ USDT |
| `/positions` | المراكز المفتوحة حالياً على المنصة |
| `/trades` | آخر الصفقات المفتوحة المسجلة في قاعدة البيانات |
| `/pnl` | أداء اليوم (%) |
| `/pause` | إيقاف فتح صفقات جديدة (الحالية تفضل شغالة) |
| `/resume` | استئناف فتح صفقات جديدة |
| `/dryrun_on` | تفعيل وضع التجربة فوراً بدون إيقاف السيرفر |
| `/dryrun_off` | تفعيل التداول الحقيقي فوراً |
| `/setrisk 0.5` | تعديل % المخاطرة لكل صفقة لحظياً |
| `/closeall confirm` | 🚨 إغلاق كل المراكز المفتوحة فوراً بسعر السوق |

البوت كمان بيبعتلك إشعار تلقائي بكل صفقة جديدة، وأي خطأ يحصل، وأي إيقاف تلقائي بسبب حد الخسارة اليومي.

### إعداد بوت تليجرام (خطوتين بس)
1. افتح تليجرام وابحث عن **[@BotFather](https://t.me/BotFather)** → ابعتله `/newbot` واتبع الخطوات → هيديك `TELEGRAM_BOT_TOKEN`.
2. ابحث عن **[@userinfobot](https://t.me/userinfobot)** وابعتله أي رسالة → هيديك الـ `Chat ID` بتاعك → ده اللي تحطه في `TELEGRAM_CHAT_ID`.
3. حط القيمتين دول في Railway Variables (موضح تحت).

## الاستراتيجية

- **كروس EMA**: EMA9 يقطع EMA21 (قابلة للتعديل) كإشارة اتجاه.
- **فلتر RSI**: يمنع الدخول في مناطق التشبع الشرائي/البيعي.
- **ستوب لوس / تيك بروفيت ديناميكي بـ ATR**: يتناسب تلقائياً مع تقلب كل عملة.
- **إدارة مخاطر مدمجة**: حجم صفقة محسوب من % مخاطرة ثابت، وحد أقصى للخسارة اليومية يوقف البوت تلقائياً.

## هيكل المشروع

```
bitget-scalper/
├── bot/
│   ├── __init__.py
│   ├── config.py            # كل الإعدادات من env vars
│   ├── exchange.py          # التواصل مع Bitget عبر ccxt
│   ├── strategy.py          # منطق الإشارات
│   ├── risk.py              # حجم الصفقة + حد الخسارة اليومي
│   ├── database.py          # تسجيل الصفقات في Postgres
│   ├── state.py             # حالة مشتركة (pause/dry-run/risk) بين التداول وتليجرام
│   ├── telegram_control.py  # كل أوامر التحكم عبر تليجرام
│   └── main.py              # حلقة التشغيل الرئيسية + تشغيل بوت تليجرام
├── migrations/
├── requirements.txt
├── Dockerfile
├── railway.json
├── .env.example
└── .gitignore
```

## 1. رفع المشروع على GitHub

### الطريقة السريعة (بدون أوامر Terminal)
[![Create a New Repository](https://img.shields.io/badge/GitHub-Create%20New%20Repo-181717?logo=github&style=for-the-badge)](https://github.com/new)
[![Download GitHub Desktop](https://img.shields.io/badge/Download-GitHub%20Desktop-2088FF?logo=github&style=for-the-badge)](https://desktop.github.com/)

1. اضغط زرار **Create New Repo** فوق وسمّي الريبو (مثلاً `bitget-scalper`) — سيبه Private.
2. حمّل **GitHub Desktop** لو مش عندك، وسجل دخول بحسابك.
3. من GitHub Desktop: **File → Add Local Repository** واختار مجلد `bitget-scalper` اللي فك ضغطه عندك.
4. اكتب رسالة commit واضغط **Commit to main**، بعدين **Publish repository**.

### الطريقة عن طريق الأوامر (لو مرتاح للـ Terminal)

```bash
cd bitget-scalper
git init
git add .
git commit -m "Initial commit: Bitget scalping bot"
git branch -M main
git remote add origin https://github.com/<username>/<repo-name>.git
git push -u origin main
```

⚠️ **تأكد إن ملف `.env` مش موجود في الـ commit** (موجود في `.gitignore` أصلاً). أي مفتاح API يتسرب على GitHub لازم يتلغى فوراً من Bitget.

## 2. النشر على Railway

### أ) إنشاء المشروع
اضغط الزرار ده بعد ما تحدّث الرابط باسم حسابك وريبوك (في أول الصفحة):

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/<username>/<repo-name>)

أو يدوياً:
1. ادخل على [railway.app](https://railway.app) وسجل دخول.
2. **New Project → Deploy from GitHub repo** واختار الريبو اللي رفعته.
3. Railway هيكتشف الـ `Dockerfile` تلقائياً ويبني الصورة.

### ب) إضافة قاعدة بيانات Postgres
1. جوا نفس المشروع: **New → Database → Add PostgreSQL**.
2. Railway هيضيف تلقائياً متغير `DATABASE_URL` ويربطه بالسيرفس بتاعك (لو الاتنين في نفس الـ Project، فعّل "Reference Variable" لتوصيل `DATABASE_URL` للبوت).

### ج) إضافة المتغيرات السرية (Variables)
في تبويب **Variables** بتاع الـ service، ضيف كل متغير من `.env.example`:

| المتغير | ملاحظة |
|---|---|
| `BITGET_API_KEY` | من إعدادات API في Bitget |
| `BITGET_API_SECRET` | من إعدادات API في Bitget |
| `BITGET_API_PASSPHRASE` | حددتها وقت إنشاء الـ API key |
| `DRY_RUN` | `false` للتداول الحقيقي، `true` للتجربة بدون تنفيذ (تقدر تغيرها لايف بأمر `/dryrun_on` أو `/dryrun_off` من تليجرام) |
| `SYMBOLS` | مثال: `BTC/USDT:USDT,ETH/USDT:USDT` |
| `MAX_DAILY_LOSS_PCT`, `RISK_PER_TRADE_PCT`, `LEVERAGE` | ضبط المخاطرة |
| `TELEGRAM_BOT_TOKEN` | التوكن من BotFather |
| `TELEGRAM_CHAT_ID` | الـ Chat ID بتاعك من userinfobot |

**نصيحة أمان**: صلاحيات الـ API key في Bitget خليها Trade فقط، وامنع صلاحية السحب (Withdraw) نهائياً.

### د) التشغيل
Railway هيشغل الأمر `python -m bot.main` تلقائياً (معرف في `Dockerfile`). راقب اللوجات من تبويب **Deployments → Logs**.

## 3. التشغيل محلياً (اختياري للتجربة)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # واملأ القيم
export $(cat .env | xargs)   # أو استخدم python-dotenv
python -m bot.main
```

## تنبيهات مهمة

- هذا الكود أداة تقنية فقط، مش نصيحة استثمارية. التداول بالرافعة المالية عالي المخاطر ويمكن يؤدي لخسارة كامل رأس المال.
- جرّب `DRY_RUN=true` كام يوم قبل ما تحول لـ `false`، حتى لو قررت تبدأ حقيقي بعد كده — عشان تتأكد إن الاستراتيجية والاتصال شغالين صح.
- راجع وعدّل باراميترات `EMA_FAST/EMA_SLOW/RSI/ATR` و `RISK_PER_TRADE_PCT` حسب رأس مالك وتحملك للمخاطرة.
- تأكد من صلاحيات الـ API محدودة بالتداول فقط بدون سحب.
