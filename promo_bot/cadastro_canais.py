"""Verifica se um post dos canais (fumotom, promotom, pinchei, pechinchou, apromodaluh, etc.)
bate com algum cadastro de cliente, e avisa o Oscar pelo @Achadosfutricamonitorabot pra ele
criar a promocao na mao (NAO cria nada sozinho)."""
import json
import os
import queue
import re
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

# ---------------------------------------------------------------------------------------------
# 27/11: ALERTA DE BUG -- todo post (dos canais de promoção e dos canais_cadastro) que tiver a palavra
# BUG (bug, bugs, bugou, bugado, bugada, "tá bugado"...) vira um aviso NA HORA no @Achadosfutricamonitorabot.
# Fila e linha de execução próprias: não espera a conferência dos cadastros e nunca segura o ciclo.
# ---------------------------------------------------------------------------------------------
BUG_RE = re.compile(r"(?<![a-z0-9])bug(?:s|ou|ado|ada|ados|adas|adao|adassa|zinho)?(?![a-z0-9])", re.I)
_FILA_BUG = queue.Queue(maxsize=200)
_VISTOS_BUG = set()
_THREAD_BUG = {"t": None}


def tem_bug(texto):
    return bool(BUG_RE.search(texto or ""))


def _telegram(token, chat, msg, html=True):
    dados = {"chat_id": chat, "text": msg, "disable_web_page_preview": False}
    if html:
        dados["parse_mode"] = "HTML"
    body = json.dumps(dados).encode()
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=20)
    return True


def _enviar_monitor(msg, log=print):
    token, chat = os.environ.get("BOT_MONITOR_TOKEN", ""), os.environ.get("CHAT_MONITOR_ID", "")
    if not token or not chat:
        return False
    return _telegram(token, chat, msg, html=True)


ROTULO_ROBO = "@AchadosFutricaBot"
ROTULO_MONITORA = "@Achadosfutricamonitorabot"
_BLOQUEADOS = set()        # destinos cujo token NAO e do bot esperado (conferido no getMe): nunca recebem alerta


def destinos_bug():
    """28/11: para onde vai o alerta de BUG, na ordem de tentativa: [(rotulo, token, chat)].
    1o o @AchadosFutricaBot (secret BOT_ROBO_TOKEN; chat = CHAT_ROBO_ID ou o mesmo chat do monitora, que e o seu Telegram);
    2o o @Achadosfutricamonitorabot como reserva (o aviso nunca se perde se o 1o faltar ou falhar).
    Um destino cujo token e de OUTRO bot (ver verificar_bots) fica de fora."""
    chat_m = os.environ.get("CHAT_MONITOR_ID", "")
    out = []
    tr = os.environ.get("BOT_ROBO_TOKEN", "")
    chat_r = os.environ.get("CHAT_ROBO_ID", "") or chat_m
    if tr and chat_r and ROTULO_ROBO not in _BLOQUEADOS:
        out.append((ROTULO_ROBO, tr, chat_r))
    tm = os.environ.get("BOT_MONITOR_TOKEN", "")
    if tm and chat_m and ROTULO_MONITORA not in _BLOQUEADOS:
        out.append((ROTULO_MONITORA, tm, chat_m))
    return out


def _getme(token):
    """Dono do token (username do bot) segundo o Telegram. O token nunca vai para o log."""
    with urllib.request.urlopen("https://api.telegram.org/bot%s/getMe" % token, timeout=15) as r:
        d = json.loads(r.read().decode())
    return ((d.get("result") or {}).get("username") or "")


_VERIFICADO = {"feito": False}


def verificar_bots(log=print, est=None):
    """28/11: CONFERE de quem e cada token antes de usar, para nunca misturar os robos:
      - BOT_ROBO_TOKEN tem que ser do @AchadosFutricaBot; se for de outro bot (ex.: monitora/dicas), o destino e BLOQUEADO
        (o alerta de BUG vai so pelo monitora) e o aviso sai no monitora;
      - BOT_MONITOR_TOKEN tem que ser do @Achadosfutricamonitorabot (so avisa, nao bloqueia: nao pode ficar sem alerta);
      - BOT_DICAS_TOKEN: so registra de quem e.
    Se o robo estiver certo, manda UMA confirmacao no chat dele (guardada no estado: nao repete)."""
    if _VERIFICADO["feito"]:
        return dict(_VERIFICADO.get("nomes") or {})
    _VERIFICADO["feito"] = True
    toks = {"robo": os.environ.get("BOT_ROBO_TOKEN", ""), "monitora": os.environ.get("BOT_MONITOR_TOKEN", ""),
            "dicas": os.environ.get("BOT_DICAS_TOKEN", "")}
    esperado = {"robo": ROTULO_ROBO.lstrip("@"), "monitora": ROTULO_MONITORA.lstrip("@")}
    nomes, problemas = {}, []
    for k, t in toks.items():
        if not t:
            continue
        try:
            nomes[k] = _getme(t)
        except Exception as e:  # noqa
            log("  aviso: nao consegui conferir de quem e o token %s: %r" % (k, e))
    _VERIFICADO["nomes"] = dict(nomes)
    if nomes:
        log("  bots dos tokens: " + ", ".join("%s=@%s" % (k, v) for k, v in nomes.items()))
    if "robo" in nomes and nomes["robo"].lower() != esperado["robo"].lower():
        _BLOQUEADOS.add(ROTULO_ROBO)
        problemas.append("o secret BOT_ROBO_TOKEN e do bot @%s, e NAO do %s. O alerta de BUG nao vai usar esse token (vai pelo monitora). "
                         "Troque o secret pelo token do %s (BotFather)." % (nomes["robo"], ROTULO_ROBO, ROTULO_ROBO))
    if "monitora" in nomes and nomes["monitora"].lower() != esperado["monitora"].lower():
        problemas.append("o secret BOT_MONITOR_TOKEN e do bot @%s, e nao do %s." % (nomes["monitora"], ROTULO_MONITORA))
    vistos = {}
    for k, t in toks.items():
        if t and t in vistos:
            problemas.append("os secrets %s e %s tem o MESMO token." % (vistos[t], k))
        vistos.setdefault(t, k) if t else None
    for p in problemas:
        log("  ATENCAO: " + p)
        try:
            _enviar_monitor("\u26a0\ufe0f <b>Token conferido no robo</b>: " + _esc(p))
        except Exception as e:  # noqa
            log("  aviso: nao consegui avisar o problema do token: %r" % e)
    if "robo" in nomes and ROTULO_ROBO not in _BLOQUEADOS and est is not None:
        if est.d.get("_bug_robo_ok") != nomes["robo"]:
            chat = os.environ.get("CHAT_ROBO_ID", "") or os.environ.get("CHAT_MONITOR_ID", "")
            try:
                _telegram(toks["robo"], chat, "\u2705 Token conferido: aqui e o %s. Os alertas de BUG (com a mensagem pronta) "
                          "do robo de promocoes vao chegar neste chat." % ROTULO_ROBO, html=False)
                est.d["_bug_robo_ok"] = nomes["robo"]
                log("  confirmacao enviada no chat do %s" % ROTULO_ROBO)
            except Exception as e:  # noqa
                log("  aviso: o %s nao conseguiu te mandar mensagem (%r): aperte /start nele" % (ROTULO_ROBO, e))
                try:
                    _enviar_monitor("\u26a0\ufe0f O %s nao conseguiu te mandar mensagem: abra o chat dele e aperte /start. "
                                    "Enquanto isso o alerta de BUG vai pelo monitora." % ROTULO_ROBO)
                except Exception:  # noqa
                    pass
    return nomes


BUG_CABECALHO = "\U0001F41E ACHADO BUGADO! Corre, pode acabar a qualquer momento!"
BUG_RODAPE = ""
_SO_CANAL = re.compile(r"^\s*(?:@\w+|(?:https?://)?t\.me/\S+)\s*$", re.I)
_TME = re.compile(r"(?:https?://)?t\.me/\S+", re.I)


def mensagem_pronta_bug(txt, limite=900):
    """28/11: a MENSAGEM PRONTA do BUG: o post do canal limpo (sem @canal nem t.me de outro canal), com
    cabecalho de BUG, pronta para copiar/encaminhar. Corta por linha (nunca no meio de um link)."""
    linhas = []
    for l in (txt or "").splitlines():
        l = l.rstrip()
        if _SO_CANAL.match(l):
            continue
        l = _TME.sub("", l).rstrip()
        l = re.sub(r"(?:\s|^)(?:via|fonte|canal|origem)\s*:?\s*$", "", l, flags=re.I).rstrip()   # sobra de "via t.me/..."
        linhas.append(l)
    corpo, usado = [], 0
    for l in linhas:
        if usado + len(l) + 1 > limite and corpo:
            break
        corpo.append(l)
        usado += len(l) + 1
    corpo = re.sub(r"\n{3,}", "\n\n", "\n".join(corpo)).strip()
    if not corpo:
        return ""
    partes = [BUG_CABECALHO, corpo]
    if BUG_RODAPE:
        partes.append(BUG_RODAPE)
    return "\n\n".join(partes)


def _enviar_bug(canal, pid, txt, log=print):
    """Manda o alerta + a mensagem pronta. Tenta o @AchadosFutricaBot e, se faltar/falhar, o monitora. Volta o rotulo do destino."""
    resumo = _esc((txt or "").strip()[:600])
    alerta = ("\U0001F41E <b>BUG encontrado</b> \u00b7 canal <b>%s</b>" % _esc(canal) + chr(10) + chr(10) + resumo +
              chr(10) + chr(10) + "https://t.me/%s/%s" % (canal, pid))
    pronta = mensagem_pronta_bug(txt)
    if pronta:
        alerta += (chr(10) + chr(10) + "\u2709\ufe0f <b>Mensagem pronta</b> na mensagem de baixo. Os links sao do canal de origem: "
                   "passe pelo Divulgador para sair com o seu afiliado.")
    erros = []
    for rotulo, token, chat in destinos_bug():
        msg = alerta if not erros else ("\u26a0\ufe0f (nao consegui avisar no @AchadosFutricaBot: %s)" % _esc(str(erros[0])[:80]) + chr(10) + chr(10) + alerta)
        ok = False
        for tent in (1, 2, 3):
            try:
                _telegram(token, chat, msg, html=True)
                ok = True
                break
            except Exception as e:  # noqa
                erros.append("%s: %r" % (rotulo, e))
                time.sleep(2 * tent)
        if not ok:
            continue
        if pronta:
            for tent in (1, 2):
                try:
                    _telegram(token, chat, pronta, html=False)
                    break
                except Exception as e:  # noqa
                    log("  aviso: mensagem pronta do BUG %s:%s nao saiu em %s: %r" % (canal, pid, rotulo, e))
                    time.sleep(2)
        return rotulo
    raise RuntimeError("; ".join(erros) or "sem destino para o alerta de BUG")


def _esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _trabalhar_bug():
    while True:
        canal, pid, txt = _FILA_BUG.get()
        try:
            dest = _enviar_bug(canal, pid, txt, log=_log_fundo)
            _log_fundo("  BUG avisado em %s: %s:%s" % (dest, canal, pid))
        except Exception as e:  # noqa
            _log_fundo("  aviso: alerta de BUG falhou em %s:%s: %r" % (canal, pid, e))
        finally:
            _FILA_BUG.task_done()


def enfileirar_bugs(posts_por_canal, est, log=print, agora=None):
    """Procura a palavra BUG nos posts novos e avisa no monitora (em segundo plano). Volta na hora."""
    if not destinos_bug():
        return 0
    t = _THREAD_BUG["t"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_trabalhar_bug, name="alerta_bug", daemon=True)
        t.start()
        _THREAD_BUG["t"] = t
    agora = agora or time.time()
    n = 0
    for canal, posts in (posts_por_canal or {}).items():
        for p in posts:
            pid, txt = p[0], p[1]
            quando = p[2] if len(p) > 2 else None
            if not tem_bug(txt):
                continue
            chave = "bug:%s:%s" % (canal, pid)
            if chave in _VISTOS_BUG or est.ja_fez(chave):
                continue
            _VISTOS_BUG.add(chave)
            est.marcar(chave, "bug_avisado")
            if quando and agora - quando > 3600:          # bug com mais de 1 h já esgotou
                continue
            try:
                _FILA_BUG.put_nowait((canal, pid, txt))
                n += 1
            except queue.Full:
                log("  aviso: fila do alerta de BUG cheia; %s ficou sem aviso" % chave)
    if len(_VISTOS_BUG) > 20000:
        _VISTOS_BUG.clear()
    return n


class BugVigia:
    """28/11: alerta de BUG em TEMPO REAL. Escuta os canais pela sua conta (evento do Telegram) e, na hora que sai
    um post com a palavra BUG, manda o alerta + a mensagem pronta. A leitura dos canais no ciclo continua como
    reserva (canal onde a conta nao esta inscrita nao gera evento); o mesmo post nunca avisa 2x."""

    def __init__(self, client, canais, est, log=print):
        self.c, self.est, self.log = client, est, log
        self.canais = [str(x).lstrip("@") for x in dict.fromkeys(canais or []) if x]
        self._nomes = {c.lower(): c for c in self.canais}
        self._handler = None
        self.avisos = 0

    def tratar(self, canal, pid, txt, quando=None):
        nome = self._nomes.get((canal or "").lower(), canal)
        n = enfileirar_bugs({nome: [(pid, txt, quando)]}, self.est, self.log)
        self.avisos += n
        return n

    async def _evento(self, ev):
        try:
            txt = ev.message.message or ""
            if not tem_bug(txt):
                return
            ch = await ev.get_chat()
            d = getattr(ev.message, "date", None)
            self.tratar(getattr(ch, "username", "") or "", ev.message.id, txt, d.timestamp() if d else None)
        except Exception as e:  # noqa
            self.log("aviso: alerta de BUG (evento): %r" % e)

    def ligar(self):
        if not self.canais or not destinos_bug():
            return False
        import asyncio
        from telethon import events
        ok = []
        for canal in self.canais:         # so os canais que a conta enxerga: um nome ruim nao derruba o alerta dos outros
            try:
                self.c.loop.run_until_complete(asyncio.wait_for(self.c.get_input_entity(canal), timeout=20))
                ok.append(canal)
            except Exception as e:  # noqa
                self.log("aviso: alerta de BUG em tempo real: nao enxergo @%s (%r); fica so na leitura do ciclo" % (canal, e))
        if not ok:
            return False
        self._handler = self._evento
        self.c.add_event_handler(self._handler, events.NewMessage(chats=ok))
        self.log("alerta de BUG em tempo real ligado em %d canais (destino: %s)" % (len(ok), destinos_bug()[0][0]))
        return True

    def desligar(self):
        if self._handler:
            try:
                self.c.remove_event_handler(self._handler)
            except Exception:  # noqa
                pass
            self._handler = None
