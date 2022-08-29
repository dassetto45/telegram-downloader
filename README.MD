# Telegram Media downloader

Media downloader written in python

## Installing

```shell
pip3 install telethon
```

## Running

```shell
python downloader.py
```

Get API hash and API id from https://my.telegram.org, under API Development and paste when prompted. 

If you want to recieve notifications when downloads are over, answer Y and read further

When prompted input the chat/channel from where you want to download all media and hit enter

## Enable Notifications

It's possible to enable notification directly on your account.
In order to do so you have to:

1. Have a Telegram account using your phone number

2. Go into settings (via web or app) and set a username (if you already haven't)

This is needed to obtain an id which your bot will use to send messages to

3. Send a message to RawDataBot to get your id

Just search in app (or web) for **RawDataBot** and send any message (hi will do). Take a note of your id.

4. Create your bot

search for **BotFather** and send the message /start. Help is displayed. Send the message /newbot and follow the instructions. Take a note of your _token_ to access the HTTP API

If you get errors with requests do the follwing and try again

```shell
pip install requests
```

### Known issues

If you encounter UnsupportedMedia issue take this steps:

1. uninstall existing telethon, if any

```shell
pip uninstall telethon
```

2. install telethon from git tag version

```shell
pip3 install -U https://github.com/LonamiWebs/Telethon/archive/v1.24.zip
```

3. install cryptg (not sure if it's needed but it's nice to have it anyway)

```shell
pip install --upgrade cryptg~=0.3
```
