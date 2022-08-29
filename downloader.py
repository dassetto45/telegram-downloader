# -*- coding: utf-8 -*-
from datetime import datetime
import os
import sys
import json
from telethon import TelegramClient, sync


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
        print("Insert your Api ID: ")
        input_api_id = input()
        print("Insert your Api HASH: ")
        input_api_hash = input()

        configObject = []
        configObject.append({
            "api_id": int(input_api_id),
            "api_hash": input_api_hash
        })
        jsonString = json.dumps(configObject, default=str)
        with open("config.json", 'w') as file:
            file.write(jsonString)
            print("Configuration completed!")
        with open("config.json") as fp:
            listObj = json.load(fp)
            return listObj

# stampa progresso download


def callback(current, total):
    print('Scaricato', current, 'di', total,
          'bytes: {:.2%}'.format(current / total))

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


config = get_config()
api_id = config[0]['api_id']
api_hash = config[0]['api_hash']

filename = "downloaded.json"

client = TelegramClient('sessions/test_session_101',
                        api_id, api_hash,
                        )
client.start()


channelList = client.iter_dialogs()
for d in channelList:
    channelId = d.entity.id
    channelName = d.name
    print(f"{channelName}")

baseDir = "downloads/"
print('Seleziona il canale:')
nomeCanale = input()
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
