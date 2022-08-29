# -*- coding: utf-8 -*-
from datetime import datetime
import os
import json
from telethon import TelegramClient, sync

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
        print(listObject)
    else:
        listObject = []
    listObject.append({
        "id": message_id,
        "date_time": datetime.now(),
        "completed": True
    })
    writeFile(listObject, channelName, filename)


api_id = 000
api_hash = 'xxx'

filename = "downloaded.json"

client = TelegramClient('test_session_das_110',
                        api_id, api_hash,
                        )
client.start()


channelList = client.iter_dialogs()
for d in channelList:
    channelId = d.entity.id
    channelName = d.name
    print(f"{channelName}")

print('Seleziona il canale:')
nomeCanale = input()

if not os.path.exists(nomeCanale):
    os.makedirs(nomeCanale)


listObj = readFile(nomeCanale, filename)
for message in client.iter_messages(nomeCanale, 6):
    downloaded = False
    for download in listObj:
        if message.id == download['id']:
            downloaded = True
    if message.media != None and downloaded == False:
        c = client.download_media(
            message, nomeCanale, progress_callback=callback)
        buildFile(nomeCanale, filename, message.id)
