"""Confere, de vez em quando, se os bots do Divulgador (Mercado Livre/Amazon/etc.) ficaram
PARADOS numa pergunta que ninguem respondeu (ex.: apos uma execucao do robo principal cair no meio).
So mexe quando tem certeza que esta parado ha muito tempo -- NUNCA cancela uma promocao em andamento.
Roda sozinho (workflow .github/workflows/watchdog-divulgador.yml), de 2 em 2 horas, sem depender do Claude."""
import os
import sys
import time

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

from promo_bot import config as C
from promo_bot.telethon_conv import ConvTelethon, resolver_bots

PARADO_MIN = 20  # so mexe se a ultima mensagem do bot estiver parada ha mais tempo que isto


def log(s):
    print(time.strftime("%H:%M:%S"), s, flush=True)


def parece_travado(msg):
    """True so quando a ultima mensagem e uma PERGUNTA pendente do bot (nunca numa tela 'processando...')."""
    if msg is None or msg.out:
        return False
    txt = (msg.message or "").strip()
    if not txt and not msg.buttons:
        return False
    baixo = txt.lower()
    if any(p in baixo for p in ("processando", "carregando", "aguarde", "buscando", "analisando", "gerando")):
        return False  # etapa intermediaria: nunca mexe aqui, mesmo que demore
    if txt.endswith("/cancelar"):
        return True
    if msg.buttons:
        return True  # tela com botoes esperando clique (ex.: confirmar cancelamento, escolher plataforma)
    return False


def main():
    cfg = C.carregar()
    if cfg.get("mode") == "off":
        log("modo OFF: nada a fazer.")
        return 0
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    sess = os.environ.get("TG_SESSION")
    if not (api_id and api_hash and sess):
        log("faltam TG_API_ID/TG_API_HASH/TG_SESSION: nada a fazer.")
        return 0
    bots_cfg = cfg.get("bots_divulgador") or [cfg["bot_divulgador"]]
    presos, verificados = [], []
    with TelegramClient(StringSession(sess), int(api_id), api_hash) as client:
        bots = resolver_bots(client, bots_cfg, log) or bots_cfg
        for b in bots:
            try:
                msgs = client.get_messages(b, limit=1)
            except Exception as e:  # noqa
                log("aviso: nao consegui ler @%s: %r" % (b, e))
                continue
            m = msgs[0] if msgs else None
            if m is None or not m.date:
                continue
            idade_min = (time.time() - m.date.timestamp()) / 60
            verificados.append(b)
            if not parece_travado(m):
                log("@%s: ok" % b)
                continue
            if idade_min < PARADO_MIN:
                log("@%s: parece parado mas e recente (%.0f min) -- pode estar em andamento, nao mexo" % (b, idade_min))
                continue
            log("@%s: parado ha %.0f min sem resposta -- destravando com /cancelar" % (b, idade_min))
            try:
                conv = ConvTelethon(client, b)
                conv.log = log
                ok = conv.cancelar_tudo()
                log("  @%s %s" % (b, "destravado" if ok else "tentei, mas nao confirmou"))
                if ok:
                    presos.append(b)
            except Exception as e:  # noqa
                log("  aviso: falha ao destravar @%s: %r" % (b, e))
        if presos and len(presos) == len(verificados) and len(verificados) > 1:
            try:
                client.send_message("me", "⚠️ Os %d bots do Divulgador estavam travados (sem resposta ha mais de %d min) -- acabei de destravar. Se isso voltar a acontecer com frequencia, pode ser algo maior parado no robo principal." % (len(presos), PARADO_MIN))
            except Exception:  # noqa
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
