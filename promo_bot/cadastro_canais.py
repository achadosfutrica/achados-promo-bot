"""Verifica se um post dos canais (fumotom, promotom, pinchei, pechinchou, apromodaluh, etc.)
bate com algum cadastro de cliente, e avisa o Oscar pelo @Achadosfutricamonitorabot pra ele
criar a promocao na mao (NAO cria nada sozinho)."""
import json
import os
import queue
import threading
import time
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


# ---------------------------------------------------------------------------------------------
# 27/11 (correcao do travamento de 16:18-16:58): a conferencia dos cadastros NAO pode mais segurar o
# robo. Antes, cada post novo era mandado ao Apps Script UM POR VEZ dentro do ciclo (5-20 s cada); com
# a fila acumulada no inicio da execucao isso parou a geracao das promocoes por ~40 min. Agora os posts
# entram numa fila e uma linha de execucao separada confere e avisa; o ciclo segue na hora.
# ---------------------------------------------------------------------------------------------
_FILA = queue.Queue(maxsize=400)
_VISTOS = set()
_THREAD = {"t": None}
MAX_IDADE_SEG = 3 * 3600      # post mais velho que isso nao e conferido (o cliente ja perdeu a oferta)


def _log_fundo(s):
    # NAO usa o log() do robo: ele marca "sinal de vida" e esconderia um travamento do ciclo principal
    print(time.strftime("%H:%M:%S"), s, flush=True)


def _trabalhar():
    while True:
        canal, pid, txt = _FILA.get()
        try:
            t0 = time.time()
            achados = verificar(txt, canal, log=_log_fundo)
            if achados:
                link = "https://t.me/%s/%s" % (canal, pid)
                _log_fundo("  cadastro bateu em %s:%s: %s" % (canal, pid, ", ".join(a.get("nome") or "?" for a in achados)))
                avisar(achados, canal, txt, link, log=_log_fundo)
            if time.time() - t0 > 30:
                _log_fundo("  aviso: conferencia de cadastro lenta (%ds) em %s:%s" % (time.time() - t0, canal, pid))
        except Exception as e:  # noqa
            _log_fundo("  aviso: verificacao de cadastro falhou em %s:%s: %r" % (canal, pid, e))
        finally:
            _FILA.task_done()


def _garantir_thread():
    t = _THREAD["t"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_trabalhar, name="cadastro_canais", daemon=True)
        t.start()
        _THREAD["t"] = t


def enfileirar(posts_por_canal, est, log=print, agora=None):
    """Poe os posts novos na fila de conferencia e VOLTA NA HORA (nunca espera o Apps Script)."""
    if not configurado():
        return 0
    _garantir_thread()
    agora = agora or time.time()
    n = 0
    for canal, posts in (posts_por_canal or {}).items():
        for p in posts:
            pid, txt = p[0], p[1]
            quando = p[2] if len(p) > 2 else None
            chave = "cad:%s:%s" % (canal, pid)
            if chave in _VISTOS or est.ja_fez(chave):
                continue
            _VISTOS.add(chave)
            est.marcar(chave, "cad_verificado")
            if quando and agora - quando > MAX_IDADE_SEG:
                continue
            try:
                _FILA.put_nowait((canal, pid, txt))
                n += 1
            except queue.Full:
                log("  aviso: fila da conferencia de cadastro cheia; %s ficou sem conferir" % chave)
    if len(_VISTOS) > 20000:
        _VISTOS.clear()
    return n
