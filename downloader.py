# -*- coding: utf-8 -*-
from datetime import datetime
import os
import sys
import requests
import json
import re
from telethon.sync import TelegramClient


def get_config():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    if not os.path.exists("sessions"):
        os.makedirs("sessions")
    if os.path.exists("config.json"):
        try:
            with open("config.json") as fp:
                config = json.load(fp)
                return config
        except:
            print("Configuration file config.json not found")
            sys.exit(1)
    else:
        print("Let's make some config!")
        config = []
        print("Insert full path for your downloads: (default path is ./download)")
        path = input()
        if path == "":
            path = "./download"
        print("Insert your Api ID: ")
        input_api_id = input()
        print("Insert your Api HASH: ")
        input_api_hash = input()
        print(
            "Notify me when downloads are done (you'll need your account id and a bot token) [y/N]")
        input_notify = input()
        if input_notify == "":
            input_notify = False
        else:
            input_notify = True
            print("Your user id: ")
            input_user_id = input()
            print("Your BOT token: ")
            input_bot_token = input()
        configObject = []
        if input_notify == False:
            configObject.append({
                "api_id": int(input_api_id),
                "api_hash": input_api_hash,
                "notify": input_notify,
                "path": path
            })
        else:
            configObject.append({
                "api_id": int(input_api_id),
                "api_hash": input_api_hash,
                "notify": input_notify,
                "user_id": int(input_user_id),
                "bot_token": input_bot_token,
                "path": path
            })
        jsonString = json.dumps(configObject, default=str)
        with open("config.json", 'w') as file:
            file.write(jsonString)
            print("Configuration completed!")
        with open("config.json") as fp:
            listObj = json.load(fp)
            return listObj

# stampa progresso download

def bytes_to_mb(byte_amount):
    return byte_amount / (1024 * 1024)

def callback(current, total):
    #print('Downloaded', current, 'of', total,
    #      'bytes: {:.2%}'.format(current / total))
    print(f"Downloaded {bytes_to_mb(current)} of {bytes_to_mb(total):.2%} MB: {current/total:.2%}")

# legge file sincronizzazione


def readFile(channelName, filename):
    try:
        with open(channelName + "/" + filename) as fp:
            listObj = json.load(fp)
            return listObj
    except:
        return []

# scrive file sincronizzazione


def writeFile(aList, channelName, filename):
    jsonString = json.dumps(aList, default=str)
    with open(channelName + "/" + filename, 'w') as file:
        file.write(jsonString)
        return True

# aggiunge oggetti al file sincronizzazione


def buildFile(channelName, filename, message_id):
    if os.path.exists(channelName + "/" + filename):
        listObject = readFile(channelName, filename)
    else:
        listObject = []
    listObject.append({
        "id": message_id,
        "date_time": datetime.now(),
        "completed": True
    })
    writeFile(listObject, channelName, filename)


def sendNotification(config, channelName):
    token = config[0]['bot_token']
    userId = config[0]['user_id']
    url = f"https://api.telegram.org/bot{token}"
    params = {"chat_id": userId,
              "text": "Download from " + channelName + " is over"}
    r = requests.get(url + "/sendMessage", params=params)


config = get_config()
api_id = config[0]['api_id']
api_hash = config[0]['api_hash']
path = config[0]['path']
filename = "downloaded.json"

client = TelegramClient('sessions/test_session_101',
                        api_id, api_hash,
                        )
client.start()

lista = []
channelList = client.iter_dialogs()
for d in channelList:
    channelId = d.entity.id
    channelName = d.name
    lista.append(channelName)

lista.sort()
for ch in lista:
    print(f"{ch}")

baseDir = path
print('Select what chat/channel you want to download media files: ')
nomeCanale = input()
#nomeCorretto = re.sub(r'[^\w_. -]', '', nomeCanale)
fullPath = baseDir + nomeCanale

if not os.path.exists(fullPath):
    os.makedirs(fullPath)


listObj = readFile(fullPath, filename)
for message in client.iter_messages(nomeCanale):
    downloaded = False
    for download in listObj:
        if message.id == download['id']:
            downloaded = True
    if message.media != None and downloaded == False:
        c = client.download_media(
            message, fullPath, progress_callback=callback)
        buildFile(fullPath, filename, message.id)
print("Download completed. You can find your files in " + fullPath)
if config[0]['notify'] == True:
    sendNotification(config, nomeCanale)
