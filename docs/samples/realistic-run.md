# Realistic lead run: what really happened vs what the agent concluded

Simulated leads went through the real agent (intake, our own sender with its send window, tracked links, open image, visit beacon, webhooks, worker, AI reply reading with the local rule-based model). Each lead behaves like a real person, and its inbox brings its own machines (Apple Mail, Microsoft Defender, bank security sandboxes).

## Summary

| | |
|---|---|
| Leads | 15 |
| Emails sent | 47 |
| Emails a person really read | 12 (people who read anything: 8 of 15) |
| Opens logged / counted as a person / machine or bot | 33 / 11 / 22 |
| Link clicks: by people / by machines | 3 / 55 |
| Clicks the agent counted as a person | 7 |
| Engaged page visits (verified reading) | 2 |
| Replies read by the AI | 5 |
| Meetings booked | 2 |
| Hot-lead alerts to sales / of which wrong | 1 / 0 |
| 'Check first' alerts / of which on machines only | 3 / 1 |
| Really interested leads caught | 3 of 3 |
| Outcome as expected | 15 of 15 |

## Per lead

| # | Lead (truth) | Inbox | Emails read / sent | Agent saw | Alerts | Outcome | Expected |
|---|---|---|---|---|---|---|---|
| 1 | **Never reads (Apple Mail)**: Never opens any email. Apple Mail still loads every image on delivery. | Apple Mail (Privacy Protection) | 0 / 4 | opens: 3 bot, 1 machine; clicks: none; visits: 0; replies: none; score 0 | none | silent | ✅ silent |
| 2 | **Never reads (bank on Microsoft 365)**: Never opens any email. Microsoft Defender opens the image and every link of each email within a minute. | Outlook / Microsoft 365 + Defender | 0 / 4 | opens: 4 bot; clicks: 4 bot; visits: 0; replies: none; score 0 | none | silent | ✅ silent |
| 3 | **Never reads (Gmail)**: Never opens any email. | Gmail / Google Workspace | 0 / 4 | opens: none; clicks: none; visits: 0; replies: none; score 0 | none | silent | ✅ silent |
| 4 | **Never reads (bank sandbox that follows every link)**: Never opens any email. The bank's gateway opens every link, including the hidden one, 4 minutes after delivery. | Bank gateway sandbox (follows every link) | 0 / 4 | opens: 4 bot; clicks: 4 bot; visits: 0; replies: none; score 0 | none | silent | ✅ silent |
| 5 | **Never reads (smart bank sandbox)**: Never opens any email. The gateway skips hidden links and clicks two real links 7 minutes after delivery, running JavaScript. | Bank gateway sandbox (skips hidden links, delayed) | 0 / 4 | opens: 4 human; clicks: 4 human; visits: 0; replies: none; score 10 | click_check | check | ✅ check (known limit: it looks like a person clicking, so sales get a 'check first' alert, never a hot-lead alert) |
| 6 | **Reads everything, does nothing**: Opens all 4 emails, never clicks or replies. | Gmail / Google Workspace | 4 / 4 | opens: 4 human; clicks: none; visits: 0; replies: none; score 0 | none | silent | ✅ silent |
| 7 | **Clicks once, leaves in 4 seconds**: Opens email 2, clicks pricing, closes the page after 4 seconds. | Gmail / Google Workspace | 1 / 4 | opens: 1 human; clicks: 1 human; visits: 0; replies: none; score 5 | click_check | check | ✅ check |
| 8 | **Misses 2 emails, then reads properly**: Misses emails 1 and 2. Opens email 3, reads the case study for 40 s; opens email 4, reads the pricing page for 70 s. | Gmail / Google Workspace | 2 / 4 | opens: 2 human; clicks: 2 human; visits: 2; replies: none; score 15 | click_check, hot_lead, hot_no_reply | hot | ✅ hot |
| 9 | **Replies to the last email only**: Never sees emails 1 to 3. Replies to email 4, then books a demo the next day. | Outlook / Microsoft 365 + Defender | 1 / 4 | opens: 4 bot; clicks: 4 bot; visits: 0; replies: interested; score 0 | booked, handoff | booked | ✅ booked |
| 10 | **Says yes to email 1**: Reads email 1 three hours later, replies yes, books the next day. | Apple Mail (Privacy Protection) | 1 / 1 | opens: 1 machine; clicks: none; visits: 0; replies: interested; score 0 | booked, handoff | booked | ✅ booked |
| 11 | **Not now**: Reads email 2, replies that it is not the right time. | Outlook + Defender (unlisted Azure range) | 1 / 2 | opens: 2 bot; clicks: 2 bot; visits: 0; replies: not_now; score 0 | none | not_now | ✅ not_now |
| 12 | **Out of office**: An auto-reply to email 1: away until 30 November. | Gmail / Google Workspace | 0 / 4 | opens: none; clicks: none; visits: 0; replies: ooo; score 0 | none | ooo | ✅ ooo |
| 13 | **Wrong person, names a colleague**: Reads email 1, says a colleague handles this. | Bank gateway sandbox (follows every link) | 1 / 1 | opens: 1 bot; clicks: 1 bot; visits: 0; replies: wrong_person; score 0 | none | referred | ✅ referred |
| 14 | **Unsubscribes**: Reads email 2 and unsubscribes. (Defender also visits the unsubscribe link of every email.) | Outlook / Microsoft 365 + Defender | 1 / 2 | opens: 2 bot; clicks: 2 bot; visits: 0; replies: none; score 0 | none | unsubscribed | ✅ unsubscribed |
| 15 | **Mailbox no longer exists**: The address passed verification but the mailbox was closed: email 1 bounces. | Gmail / Google Workspace | 0 / 1 | opens: none; clicks: none; visits: 0; replies: none; score 0 | none | bounced | ✅ bounced |

## Timelines

**Lead 1: Never reads (Apple Mail)** (nurture, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 17.58.10.20 opens pixel of email 1
- email 2 delivered (Fri 20 Nov 15:02 India time)
- machine at 17.58.10.20 opens pixel of email 2
- email 3 delivered (Wed 25 Nov 11:08 India time)
- machine at 17.58.10.20 opens pixel of email 3
- email 4 delivered (Tue 01 Dec 15:33 India time)
- machine at 17.58.10.20 opens pixel of email 4

**Lead 2: Never reads (bank on Microsoft 365)** (nurture, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 1
- email 2 delivered (Fri 20 Nov 15:32 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 2
- email 3 delivered (Wed 25 Nov 10:59 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 3
- email 4 delivered (Tue 01 Dec 16:05 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 4

**Lead 3: Never reads (Gmail)** (nurture, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- email 2 delivered (Fri 20 Nov 15:21 India time)
- email 3 delivered (Wed 25 Nov 10:36 India time)
- email 4 delivered (Tue 01 Dec 16:05 India time)

**Lead 4: Never reads (bank sandbox that follows every link)** (nurture, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 203.0.113.40 opens booking, unsubscribe, trap, pixel of email 1
- email 2 delivered (Fri 20 Nov 15:08 India time)
- machine at 203.0.113.40 opens booking, unsubscribe, trap, pixel of email 2
- email 3 delivered (Wed 25 Nov 10:53 India time)
- machine at 203.0.113.40 opens booking, unsubscribe, trap, pixel of email 3
- email 4 delivered (Tue 01 Dec 16:01 India time)
- machine at 203.0.113.40 opens booking, unsubscribe, trap, pixel of email 4

**Lead 5: Never reads (smart bank sandbox)** (nurture, score 10)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 198.51.100.60 opens pixel, booking of email 1
- email 2 delivered (Fri 20 Nov 15:20 India time)
- machine at 198.51.100.60 opens pixel, booking of email 2
- email 3 delivered (Wed 25 Nov 11:01 India time)
- machine at 198.51.100.60 opens pixel, booking of email 3
- email 4 delivered (Tue 01 Dec 15:33 India time)
- machine at 198.51.100.60 opens pixel, booking of email 4

**Lead 6: Reads everything, does nothing** (nurture, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- person reads email 1
- email 2 delivered (Fri 20 Nov 15:30 India time)
- person reads email 2
- email 3 delivered (Wed 25 Nov 10:39 India time)
- person reads email 3
- email 4 delivered (Tue 01 Dec 15:37 India time)
- person reads email 4

**Lead 7: Clicks once, leaves in 4 seconds** (nurture, score 5)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- email 2 delivered (Fri 20 Nov 15:16 India time)
- person reads email 2
- person clicks pricing in email 2
- person reads the pricing page for 4s
- email 3 delivered (Wed 25 Nov 10:34 India time)
- email 4 delivered (Tue 01 Dec 15:46 India time)

**Lead 8: Misses 2 emails, then reads properly** (nurture, score 15)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- email 2 delivered (Fri 20 Nov 15:24 India time)
- email 3 delivered (Wed 25 Nov 10:56 India time)
- person reads email 3
- person clicks case_study in email 3
- person reads the case_study page for 40s
- email 4 delivered (Tue 01 Dec 15:48 India time)
- person reads email 4
- person clicks pricing in email 4
- person reads the pricing page for 70s

**Lead 9: Replies to the last email only** (handed_off, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 1
- email 2 delivered (Fri 20 Nov 15:06 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 2
- email 3 delivered (Wed 25 Nov 11:01 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 3
- email 4 delivered (Tue 01 Dec 16:00 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 4
- person replies to email 4
- person books a demo

**Lead 10: Says yes to email 1** (handed_off, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 17.58.10.20 opens pixel of email 1
- person replies to email 1
- person books a demo

**Lead 11: Not now** (nurture, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 20.207.73.5 opens booking, unsubscribe, trap, pixel of email 1
- email 2 delivered (Fri 20 Nov 15:30 India time)
- machine at 20.207.73.5 opens booking, unsubscribe, trap, pixel of email 2
- person replies to email 2

**Lead 12: Out of office** (nurture, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- person replies to email 1
- email 2 delivered (Mon 30 Nov 16:31 India time)
- email 3 delivered (Mon 07 Dec 15:07 India time)
- email 4 delivered (Thu 10 Dec 11:08 India time)

**Lead 13: Wrong person, names a colleague** (closed, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 203.0.113.40 opens booking, unsubscribe, trap, pixel of email 1
- person replies to email 1

**Lead 14: Unsubscribes** (suppressed, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 1
- email 2 delivered (Fri 20 Nov 15:39 India time)
- machine at 74.225.34.10 opens booking, unsubscribe, trap, pixel of email 2
- person unsubscribes from email 2

**Lead 15: Mailbox no longer exists** (suppressed, score 0)
- email 1 delivered (Mon 16 Nov 10:00 India time)
- email 1 bounces

