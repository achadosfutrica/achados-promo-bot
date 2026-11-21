"""Promoções geradas À MÃO no @di48291bot NÃO viram arte no @achadosfutricatemplatesbot.

No grupo elas chegam iguais às outras, então o robô de stories só sabe pular se receber a mensagem
ENCAMINHADA pela dona. Este módulo faz esse encaminhamento sozinho, pela sua conta:
lê a conversa com o @di48291bot e encaminha cada promoção nova ("Compre aqui" / "Story gerado") ao
@achadosfutricatemplatesbot. O que sai pelo @divulgadorinteligente2bot não é tocado (vira arte normal).
"""
import time

from . import texto as T

GATILHOS = ("compre aqui", "produto postado no seu site", "story gerado")


def eh_promocao(texto):
    n = T.norm(texto or "")
    return any(g in n for g in GATILHOS)


def escolher(msgs, ultimo_id, agora=None, horas=6):
    """msgs: [(id, recebida_do_bot:bool, texto, timestamp)] -> ids para encaminhar (em ordem)."""
    agora = agora or time.time()
    out = []
    for mid, do_bot, txt, ts in sorted(msgs):
        if mid <= (ultimo_id or 0) or not do_bot:
            continue
        if ts and agora - ts > horas * 3600:
            continue                  # mais de 6 h: o robô de stories já descarta sozinho
        if eh_promocao(txt):
            out.append(mid)
    return out


def encaminhar_manuais(client, cfg, est, log, modo="live"):
    tc = cfg.get("templates") or {}
    if not tc.get("ligado", True):
        return 0
    bot_manual, robo = tc.get("bot_manual", "di48291bot"), tc.get("robo_templates", "achadosfutricatemplatesbot")
    ultimo = int(est.d.get("tpl_ultimo") or 0)
    try:
        msgs = []
        for m in client.iter_messages(bot_manual, limit=150, min_id=ultimo):
            msgs.append((m.id, not m.out, m.message or "", m.date.timestamp() if m.date else 0))
        ids = escolher(msgs, ultimo)
        if msgs and modo != "dry":
            est.d["tpl_ultimo"] = max(x[0] for x in msgs)
        if not ids:
            return 0
        if modo == "dry":
            log("templates: %d promo(s) do @%s seriam encaminhadas ao @%s" % (len(ids), bot_manual, robo))
            return 0
        for mid in ids:
            client.forward_messages(robo, mid, bot_manual)
            time.sleep(2)
        log("templates: encaminhei %d promo(s) do @%s ao @%s (nao viram arte)" % (len(ids), bot_manual, robo))
        return len(ids)
    except Exception as e:  # noqa
        log("aviso: templates: nao consegui encaminhar do @%s: %r" % (bot_manual, e))
        return 0
