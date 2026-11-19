"""RODE UMA UNICA VEZ (no Google Colab). Gera o TG_SESSION. Nunca compartilhe o resultado."""
# !pip -q install telethon
from telethon import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID: "))
api_hash = input("API HASH: ").strip()
client = TelegramClient(StringSession(), api_id, api_hash)
await client.start()  # no Colab (top-level await); pede telefone, codigo e senha
print("\n=== COPIE O TEXTO ABAIXO (TG_SESSION) ===\n")
print(client.session.save())
await client.disconnect()
