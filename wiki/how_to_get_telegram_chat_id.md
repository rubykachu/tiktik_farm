# How to get Telegram Bot Chat ID

Tài liệu này mô tả cách lấy **chat ID dạng số** qua Bot API (dùng cho `getUpdates`, `sendMessage`, v.v.).

**Trong farm_tiktik v2 (pipeline Telegram):**

- Trường **`telegram_chat_id`** phải là **chuỗi chỉ gồm chữ số**, có thể có dấu `-` ở đầu (supergroup/channel thường là `-100…`). **Không** nhập `@username` hay link `t.me/…` vào form.
- Modal pipeline có nút **Tự động phát hiện** (gọi `getUpdates`) — chỉ hiển thị **group / supergroup / channel** có trong buffer cập nhật; chat **private** với bot **không** có trong danh sách (cần lấy ID theo bước dưới rồi dán tay).
- Nút **Gửi tin test** gửi một tin nhắn thử để kiểm tra token + chat ID.
- Worker gửi thông báo tick bằng **`sendMessage`** với `chat_id` và text thuần — **không** hỗ trợ chỉ định topic (`message_thread_id`) trong cấu hình pipeline.

**Mục lục**

1. [Create a Telegram Bot and get a Bot Token](#create-a-telegram-bot-and-get-a-bot-token)
2. [Get Chat ID for a Private Chat](#get-chat-id-for-a-private-chat)
3. [Get Chat ID for a Channel](#get-chat-id-for-a-channel)
4. [Get Chat ID for a Group Chat](#get-chat-id-for-a-group-chat)
5. [Get Chat ID for a Topic in a Group Chat (Bot API)](#get-chat-id-for-a-topic-in-a-group-chat-bot-api)

## Create a Telegram Bot and get a Bot Token

1. Open Telegram application then search for `@BotFather`
2. Click Start
3. Click Menu → `/newbot` or type `/newbot` and hit Send
4. Follow the instruction until we get message like so

   ```
   Done! Congratulations on your new bot. You will find it at t.me/new_bot.
   You can now add a description.....

   Use this token to access the HTTP API:
   63xxxxxx71:AAFoxxxxn0hwA-2TVSxxxNf4c
   Keep your token secure and store it safely, it can be used by anyone to control your bot.

   For a description of the Bot API, see this page: https://core.telegram.org/bots/api
   ```

5. So here is our bot token `63xxxxxx71:AAFoxxxxn0hwA-2TVSxxxNf4c` (make sure we don't share it to anyone).

[Back to top ↑](#how-to-get-telegram-bot-chat-id)

## Get Chat ID for a Private Chat

1. Search and open our new Telegram bot
2. Click Start or send a message
3. Open this URL in a browser `https://api.telegram.org/bot<our_bot_token>/getUpdates`

   - Prefix the token with the word `bot`
   - Example: `https://api.telegram.org/bot63xxxxxx71:AAFoxxxxn0hwA-2TVSxxxNf4c/getUpdates`

4. We will see JSON like so:

   ```json
   {
     "ok": true,
     "result": [
       {
         "update_id": 83xxxxx35,
         "message": {
           "message_id": 2643,
           "from": { "...": "..." },
           "chat": {
             "id": 21xxxxx38,
             "first_name": "...",
             "last_name": "...",
             "username": "username",
             "type": "private"
           },
           "date": 1703062972,
           "text": "/start"
         }
       }
     ]
   }
   ```

5. Read `result[0].message.chat.id` — that is the Chat ID: `21xxxxx38`
6. Try sending a message:

   `https://api.telegram.org/bot63xxxxxx71:AAFoxxxxn0hwA-2TVSxxxNf4c/sendMessage?chat_id=21xxxxx38&text=test123`

7. When the bot token and chat id are correct, the message `test123` should arrive in the private chat with the bot.

**farm_tiktik:** private chats **do not** appear in **Tự động phát hiện**; paste the numeric ID into **Chat ID** manually if you use a private chat for notifications.

[Back to top ↑](#how-to-get-telegram-bot-chat-id)

## Get Chat ID for a Channel

1. Add our Telegram bot into a channel (as admin if posting is required)
2. Post a message in the channel (so `getUpdates` can see a `channel_post`)
3. Open `https://api.telegram.org/bot<our_bot_token>/getUpdates`
4. Example JSON:

   ```json
   {
     "ok": true,
     "result": [
       {
         "update_id": 838xxxx36,
         "channel_post": {
           "message_id": 1,
           "chat": {
             "id": -1001xxxxxx062,
             "title": "....",
             "type": "channel"
           },
           "date": 1703065989,
           "text": "test"
         }
       }
     ]
   }
   ```

5. Read `channel_post.chat.id` — Chat ID: `-1001xxxxxx062`
6. Test:  
   `https://api.telegram.org/bot63xxxxxx71:AAFoxxxxn0hwA-2TVSxxxNf4c/sendMessage?chat_id=-1001xxxxxx062&text=test123`

[Back to top ↑](#how-to-get-telegram-bot-chat-id)

## Get Chat ID for a Group Chat

The easiest way to get a group chat ID is through a Telegram desktop application.

1. Open Telegram in a desktop app
2. Add our Telegram bot into the group
3. Send a message in the group
4. Right-click the message and click **Copy Message Link**

   - You may get: `https://t.me/c/194xxxx987/11/13`
   - Pattern: `https://t.me/c/{internal_id}/{topic_id}/{message_id}`
   - The segment after `/c/` (e.g. `194xxxx987`) is **not** the final API chat id by itself

5. For the Bot API, supergroups usually use **`-100` + internal numeric id**. For example, if the link shows `194xxxx987`, try `-100194xxxx987` as `chat_id` in `getUpdates` / `sendMessage` (confirm in `getUpdates` under `message.chat.id` or `my_chat_member.chat.id` after the bot receives an update from that group).
6. Test sendMessage with that numeric `chat_id` as in the sections above.

**farm_tiktik:** sau khi có ID dạng `-100…`, có thể dùng **Tự động phát hiện** trong modal (nếu group vừa có hoạt động và nằm trong `getUpdates`) hoặc dán ID vào ô **Chat ID**.

[Back to top ↑](#how-to-get-telegram-bot-chat-id)

## Get Chat ID for a Topic in a Group Chat (Bot API)

Forum groups use **topics**. To target a topic you add **`message_thread_id`** to `sendMessage` (see [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage)).

1. After **Copy Message Link**, e.g. `https://t.me/c/194xxxx987/11/13`, the topic id is often the next segment after the group part (`11` in this example).
2. Example **raw API** call:

   `https://api.telegram.org/bot<token>/sendMessage?chat_id=-100194xxxx987&message_thread_id=11&text=test123`

**farm_tiktik:** pipeline notifications **do not** send `message_thread_id` — only the main `chat_id` field from the pipeline form is used. For forum groups, notifications go to the default/main behavior of `sendMessage` for that chat (và có thể **không** đúng topic mong muốn). Prefer a non-forum group or a **channel** nếu cần một đích cố định.

[Back to top ↑](#how-to-get-telegram-bot-chat-id)
