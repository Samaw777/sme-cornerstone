# Easy logging

Every quick way of logging uses the same rules as a typed entry:
- The original input (typed values, the pasted SMS, the Telegram message) is kept as the record's source event.
- References are checked so the same transaction can't be counted twice.
- Entries by anyone without approval rights wait for approval.

## Who approves

- **Owners** and staff given the **Approve entries made by others** permission are approvers. Their entries count straight away.
- **Everyone else** can log, but their entries are held as **Awaiting approval**. They aren't counted in totals, the passport or the credit metrics until an approver approves them on the **Approvals** page.
- Rejected entries are kept for the audit trail, with the approver's reason, and never counted.
- Only approvers can amend or reverse records. Staff add a note instead.
- Owners grant the Approve permission under **Business → People**.

## Quick add (`/quick/`)

The phone's main **Log** tab:
- Sale or expense, a large amount keypad, and Cash / telebirr / Bank.
- **Favourites**: save any entry with "Save as a favourite" (e.g. "Diesel") and reuse it with one tap. **Repeat last** copies your previous entry.
- **Receipt photo**: take a photo when you log. It's shrunk on the phone before upload and attached, so the record is **evidence-supported** straight away.
- **Voice**: tap the microphone and say "expense 3200 diesel cash" in English or Amharic. Speech recognition is provided by the browser: Chrome on Android works best, and some browsers don't offer it. The sentence is filled into the form for you to check.
- **Offline**: with no signal, entries are saved on the phone (IndexedDB) and upload automatically when the connection returns. The page itself is cached by a service worker, so it opens offline once it has been visited online.

## Paste SMS (`/quick/sms/`)

Paste one or more bank or telebirr SMS notifications, with a blank line between them. For each one the app reads:
- the amount, ignoring balance and fee amounts;
- whether money came in or went out;
- the date, the reference and the other party.

You review and edit the drafts before saving. The reference stops the same SMS being saved twice, including when the same SMS was also sent to the Telegram bot.

Message formats vary by bank and change over time, so parsing is a best effort. Pasted SMS are **self-reported**, because a copied message isn't an authorised bank feed.

## Bills due

Recurring obligations (rent, loan instalments, salaries…) show on the dashboard when an instalment is due, or overdue within the last two months. **Mark paid** records the payment with the right category and links it to that instalment, so it can't be marked twice. A bill also counts as paid if a matching expense was already recorded the ordinary way: same category and amount within 15 days.

## Telegram bot

Staff send the business bot messages like:

- `sale 4500 freight Adama telebirr`
- `expense 3200 diesel cash`
- `ወጪ 2,300 ነዳጅ ትናንት` (ትናንት = yesterday)
- a receipt photo with a caption such as `expense 2500 tyres`
- a forwarded bank or telebirr SMS
- `/today` for today's totals

### Setup (once, by the owner or administrator)

1. In Telegram, open **@BotFather**, send `/newbot` and follow the steps. Copy the **token** it gives you.
2. In Vercel, under **Settings → Environment Variables**, add:

   | Key | Value |
   | --- | --- |
   | `TELEGRAM_BOT_TOKEN` | the token from BotFather |
   | `TELEGRAM_WEBHOOK_SECRET` | any long random string (letters and numbers) |
   | `TELEGRAM_BOT_USERNAME` | the bot's username, without `@` |

   Then redeploy.
3. From your computer, with the same token and secret set in the terminal, tell Telegram where to send messages:
   ```powershell
   $env:TELEGRAM_BOT_TOKEN="..."; $env:TELEGRAM_WEBHOOK_SECRET="..."; $env:DEBUG="1"
   ```
   ```powershell
   python manage.py telegram_setup https://your-site.vercel.app
   ```
4. Each person opens **Log → Telegram** in the app, taps **Get link code**, and sends `/link CODE` to the bot.

The webhook only accepts requests carrying the secret, and messages from chats that haven't been linked are ignored. Each linked chat logs with that person's own permissions: entries by non-approvers wait for approval, and photos are attached only for people with the documents permission.

## Not included

- **Reading amounts from receipt photos (OCR)** needs a paid OCR or AI service. The photo is attached as evidence, but the amount is typed.
- **A full Amharic interface** needs translation by a native speaker. The parser already understands common Amharic words (ሽያጭ, ወጪ, ገቢ, ነዳጅ, ኪራይ, ደመወዝ, ትናንት…).
