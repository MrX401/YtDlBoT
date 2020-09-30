
# YtbDownBot
Telegram bot that utilize youtube-dl functionality for downloading video directly to telegram.
Simple clone of https://t.me/VideoTubeBot.

# Dependencies
Install `ffmpeg` and `python3`.

Python3 dependencies install via `pip3 install -r requirements.txt`
# Running
For running required phone number for bypassing telegram bot api upload files limitation to 50 MB.

Set the following enviroment variables:
  1. Bot token(from Bot Father):
`BOT_API_TOKEN`
  2. IBM Cloudant credentials: 
  `CLOUDANT_USERNAME`, `CLOUDANT_PASSWORD`, `CLOUDANT_URL`
  (can be easily replaced with CouchDB: read https://python-cloudant.readthedocs.io/en/latest/getting_started.html)

Note: for deploying you must set also webhook url via calling `https://api.telegram.org/bot<bot-token>/setWebhook?url=<webhook-url>` (`webhook-url` path is `bot_domanin+/bot` like `mybot.com/bot`) Use master branch if you want to use polling instead.
