# Telegram Media downloader

Media downloader written in python

## Installing

```shell
pip3 install telethon
```

Get API hash and API id from https://my.telegram.org, under API Development.
and replace in code

```python
50 api_id = 000
51 api_hash = 'xxx'
```

### Known issue

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
