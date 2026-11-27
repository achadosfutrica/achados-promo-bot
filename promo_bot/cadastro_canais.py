"""Verifica se um post dos canais (fumotom, promotom, pinchei, pechinchou, apromodaluh, etc.)
bate com algum cadastro de cliente, e avisa o Oscar pelo @Achadosfutricamonitorabot pra ele
criar a promocao na mao (NAO cria nada sozinho)."""
import json
import os
import urllib.request


def configurado():
    """True se o workflow passou as 4 variaveis do alerta de cadastro (sem elas o alerta fica mudo)."""
    return all(os.environ.get(k) for k in ("CADASTRO_CANAIS_URL", "CADASTRO_CANAIS_CHAVE", "BOT_MONITOR_TOKEN", "CHAT_MONITOR_ID"))


def verificar(texto, canal, log=print):
    """Manda o texto do post pro Apps Script (mesmo motor de combinacao dos cadastros) e devolve
    a lista de quem bateu: [{"id":.., "nome":.., "whatsapp":.., "produto":..}, ...] (vazio se ninguem bateu
    ou se faltou configuracao/deu erro -- nunca trava o robo por causa disso)."""
    url, chave = os.environ.get("CADASTRO_CANAIS_URL", ""), os.environ.get("CADASTRO_CANAIS_CHAVE", "")
    if not url or not chave or not texto:
        return []
    try:
        body = json.dumps({"acao": "verificarCanal", "chave": chave, "texto": texto, "canal": canal}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        return d.get("achados") or [] if d.get("ok") else []
    except Exception as e:  # noqa
        log("  aviso: cadastro_canais.verificar falhou (%s): %r" % (canal, e))
        return []


def avisar(achados, canal, texto, link, log=print):
    """Manda pro Telegram (bot do monitor de precos) um aviso por cadastro batido, pro Oscar criar a promocao."""
    token, chat = os.environ.get("BOT_MONITOR_TOKEN", ""), os.environ.get("CHAT_MONITOR_ID", "")
    if not token or not chat or not achados:
        return
    resumo = (texto or "").strip().replace(chr(10), " ")[:200]
    for a in achados:
        msg = ("\U0001F514 <b>Cadastro pode ter achado</b>" + chr(10) + "%s (%s)" % (a.get("nome") or "?", a.get("whatsapp") or "sem whatsapp") + chr(10) + chr(10) + resumo + chr(10) + chr(10) + "Canal: " + canal + ((chr(10) + link) if link else ""))
        try:
            body = json.dumps({"chat_id": chat, "text": msg, "parse_mode": "HTML"}).encode()
            req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body,
                                          headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:  # noqa
            log("  aviso: nao consegui avisar cadastro %s: %r" % (a.get("id"), e))
