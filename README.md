# Binance Announcement → Telegram Auto-Post Bot

Watches Binance Telegram channels, filters posts by keyword, opens the announcement, finds the
"Join Now" link, converts UTC times to Bangladesh time (UTC+6), formats the post with AI
(everything bold except the link), and posts it with the original image to your channel.

## 1. What you need
- Telegram **API_ID / API_HASH** from https://my.telegram.org (API development tools)
- A **bot token** from @BotFather
- Your **Telegram user ID** (ask @userinfobot) → `ADMIN_ID`
- A **Supabase** project — copy the **SUPABASE_URI** from Project Settings → Database → Connection string → URI (Transaction pooler, port 6543)
- A **Gemini** API key (https://aistudio.google.com)

## 2. Database
Tables are created automatically on first start (running `schema.sql` in the SQL Editor is optional).

### Keywords start empty
`schema.sql` creates the `keywords` table but inserts **nothing** — the list starts completely
empty. You, the owner, add every keyword yourself from Telegram with `/addkw word` and remove
them with `/delkw word`. Check the live list any time with `/keywords`.

Until you add at least one keyword the bot ignores every post (no AI call, no post), so the
first thing to do after setup is send a few `/addkw` commands.

Matching is **case-insensitive whole-word/phrase** matching, so one keyword like `Tournament` also
catches "spot trading tournament". The database is the single source of truth — nothing is
hardcoded in the code.





## 3. Local setup
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python generate_session.py  # asks API_ID, API_HASH, phone, code (+2FA), then saves User_session to .env
python main.py
```
> Login code arrives inside the **Telegram app** ("Telegram" chat), not SMS. Phone format: `+8801XXXXXXXXX`.

The user account (session) listens to channels; the bot account posts. Use a spare Telegram account if you prefer.

## 4. Deploy on Render
1. Push this folder to a GitHub repo (don't commit `.env`).
2. Render → New → Blueprint → choose the repo (uses `render.yaml`), or New → Web Service:
   - Build: `pip install -r requirements.txt`
   - Start: `python main.py`
3. Add exactly these 7 variables under Environment: API_ID, API_HASH, User_session, BOT_TOKEN, ADMIN_ID, SUPABASE_URI, GEMINI_API_KEY.
4. Free web services sleep — use a paid instance or ping the URL every 10 min (e.g. UptimeRobot).

## 5. Telegram-এ বাংলা মেনু
1. গন্তব্য চ্যানেলে বটকে **Admin** বানিয়ে **Post Messages** অনুমতি দিন।
2. বটের ব্যক্তিগত চ্যাটে `/start` পাঠান। শুধু `ADMIN_ID`-এর মালিক নিয়ন্ত্রণ করতে পারবেন।
3. **🎯 গন্তব্য চ্যানেল → নির্ধারণ করুন** চাপুন, তারপর চ্যানেলের লিংক, @username বা আইডি পাঠান। **যাচাই করুন** দিয়ে অনুমতি দেখুন।
4. **📢 চ্যানেল → যোগ করুন** চাপুন এবং চ্যানেলের ঠিকানা পাঠান। **তালিকা** ও **বাদ দিন** দিয়ে তালিকা পরিবর্তন করুন।
5. **🔑 কিওয়ার্ড → যোগ করুন** চাপুন এবং একটি শব্দ বা বাক্যাংশ পাঠান। অন্তত একটি কিওয়ার্ড না থাকলে স্বয়ংক্রিয় পোস্ট যাবে না।
6. **🧪 ম্যানুয়াল টেস্ট** চাপুন এবং পোস্টের লিংক পাঠান। এটি কিওয়ার্ড ফিল্টার এড়ায় এবং আগে পাঠানো পোস্ট আবার পাঠায়।
7. **📊 বটের অবস্থা** ও **❓ সাহায্য** থেকে অবস্থা ও নির্দেশনা দেখুন।

চ্যানেল হিসেবে `@channelname`, `channelname`, `t.me/channelname`, `https://t.me/channelname` অথবা `-1001234567890` দিন। খালি সংখ্যার আইডিতে `-100` যোগ করা হয়। ব্যক্তিগত চ্যানেলে ব্যবহারকারী অ্যাকাউন্ট আগে থেকেই যুক্ত থাকতে হবে; শুধু আইডি দিয়ে নিজে থেকে প্রবেশ করা যায় না। চ্যানেল যোগ করার সময় পোস্ট বা আমন্ত্রণ লিংক নয়, চ্যানেলের ঠিকানা দিন। পরীক্ষার জন্য `https://t.me/channelname/123` ও `https://t.me/c/123456/789` গ্রহণ করা হয়।

`/cancel` অথবা **⬅️ মূল মেনু** দিয়ে কাজ বাতিল করুন। অন্য মেনু বা কমান্ডও আগের অপেক্ষা বাতিল করে। ইনপুটের সময় ১০ মিনিট; পুনরায় চালু হলে অসমাপ্ত কাজ বাতিল হয়, সংরক্ষিত সেটিংস থাকে।

### বিকল্প টেক্সট কমান্ড
`/start` `/menu` `/help` `/cancel` `/channels` `/addchannel @x` `/delchannel @x`
`/keywords` `/addkw শব্দ` `/delkw শব্দ` `/setdest @x` `/dest` `/test <পোস্ট লিংক>` `/status`

বাংলা পরিবর্তন শুধু প্রশাসনিক মেনু ও উত্তরগুলোতে; ক্যাম্পেইন পোস্টের আগের বিন্যাস অপরিবর্তিত।

## Output format
```
**🛒Spot Trading Tournament S6**
**🎁 Get Up 350 $FF**
**🎉 For All User**

**🔗 Link :** [Open✅](https://...)
**• Event Goes Live**
**at Today 4:00 PM ✅**
**• Event Will Expire**
**22 OCT at 4:00 PM ❕**
**➡️Register Event Page**
**➡️Make $500 Volume on $FF/USDT**
**➡️Done**
```
Dates are calculated in code (first UTC time = start, second = end), not by the AI.

## বাংলা গাইড
1. my.telegram.org থেকে API_ID ও API_HASH নিন, @BotFather থেকে বট টোকেন নিন।
2. Supabase-এ `schema.sql` রান করুন।
3. `python generate_session.py` চালান — API_ID, API_HASH, ফোন নম্বর (+880...) দিন। কোডটি SMS নয়, **Telegram অ্যাপের "Telegram" চ্যাটে** আসবে। 2FA থাকলে পাসওয়ার্ড দিন। শেষে `Y` দিলে User_session নিজে থেকেই `.env`-এ সেভ হবে।
4. বটকে আপনার চ্যানেলে **Admin** বানান (Post Messages অনুমতি সহ)।
5. বটকে `/setdest @আপনার_চ্যানেল` পাঠান, তারপর `/dest` দিয়ে যাচাই করুন।
6. `/addchannel` ও `/addkw` দিয়ে চ্যানেল ও কিওয়ার্ড যোগ করুন।

**সমস্যা সমাধান:** পোস্ট না এলে `/dest` দেখুন (বট অ্যাডমিন কিনা), কিওয়ার্ড মিলছে কিনা দেখুন,
এবং `/test <পোস্ট লিংক>` দিয়ে পরীক্ষা করুন। এরর হলে বট আপনাকে মেসেজ দেবে।

## Reliability notes (audit)
- Every post is reserved in the database *before* sending, so the same post can never be published twice (even on restarts, edits or duplicate events).
- Edited channel posts are watched too; if an edit adds a keyword it is posted once.
- If the article page or the AI fails, the bot still posts using the channel text and logs the fallback. Publishing errors notify the owner.
- Keywords match whole words, case-insensitive (`list` matches "Will LIST", not "Listing").
- Run tests: `python test_bot.py`
