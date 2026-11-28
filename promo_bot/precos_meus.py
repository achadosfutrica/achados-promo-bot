"""27/11: ALERTA DE MENOR PRECO -- quando um canal (os de promocao E os canais_cadastro) posta um produto pelo
MESMO preco ou MAIS BAIXO do que o menor preco que o Oscar ja postou daquele produto no grupo dele, avisa no
bot de DICAS DE POSTAGEM (BOT_DICAS_TOKEN; sem ele, no @Achadosfutricamonitorabot).

Historico ("menor preco ja encontrado por mim") = os posts do SEU grupo (config inteligencia.meu_grupo).
  - guardado em state/precos/AAAA-MM-DD.json (um arquivo pequeno por dia -> o git nao incha);
  - aprende os ultimos `dias` (padrao 90) aos poucos, do mais novo para o mais velho (1500 posts por ciclo),
    e depois so le o que entrou de novo.

Mesmo produto:
  - mesmo codigo (ASIN / MLB / Shopee / Magalu) quando os dois tem; ou
  - nome: o titulo mais curto esta quase todo (>= 85%, minimo 3 palavras) dentro do outro E as medidas/modelos
    (palavras com numero: 128gb, 16a, 23l, 220v, a19...) do mais curto estao todas no outro.

Regras do aviso:
  - preco do canal <= seu menor preco  -> avisa ("ABAIXO" ou "IGUAL" ao seu menor);
  - IGUAL ao seu menor que voce postou nas ultimas 24 h -> nao avisa (voce acabou de postar);
  - o mesmo produto so avisa de novo se aparecer AINDA MAIS BARATO (12 h).
Tudo em memoria e em segundo plano: nunca segura o ciclo do robo.
"""
import json
import os
import queue
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from . import inteligencia as IA
from . import repetidos
from . import texto as T

PASTA = os.environ.get("PROMO_PRECOS_DIR", "state/precos")
BRT = timezone(timedelta(hours=-3))
STOP = IA.STOP | set("mais com sem por para cada und unid un pcs pecas peca x".split())

_IDX = {"k": {}, "tok": {}, "dias": {}, "sujo": set(), "carregado": False}
_PTR = {}
_FILA = queue.Queue(maxsize=200)
_THREAD = {"t": None}
_AVISADOS = {}
_VISTOS = set()


# ------------------------------------------------------------------ produto
def tokens(titulo):
    out = set()
    for w in re.findall(r"[a-z0-9]+", T.norm(titulo or "")):
        if w in STOP:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                     # tomadas = tomada
        out.add(w)
    return out


def _spec(ws):
    """Medidas/modelos/tamanhos: tudo que tem numero (128gb, 23l, a19) e letra sozinha (P/M/G)."""
    return {w for w in ws if len(w) == 1 or any(c.isdigit() for c in w)}


def mesmo_produto(a, b):
    """a, b = conjuntos de palavras dos titulos."""
    if len(a) < 3 or len(b) < 3:
        return False
    curto, longo = (a, b) if len(a) <= len(b) else (b, a)
    comum = curto & longo
    if len(comum) < 3 or len(comum) / len(curto) < 0.85:
        return False
    if not _spec(curto) <= longo:
        return False
    if not (curto - _spec(curto)) & longo:
        return False
    return True


def registro(texto):
    """(chave, preco, palavras, ids, titulo) de um post; None se nao da para identificar o produto/preco."""
    p, _, _ = T.preco(texto or "")
    if not p or p < 1:
        return None
    tit = IA.titulo(texto)
    ws = tokens(tit)
    ids = sorted(repetidos.ids(texto))
    if not ids and len(ws) < 3:
        return None
    chave = ("id:" + ids[0]) if ids else ("w:" + " ".join(sorted(ws)))
    return chave, float(p), ws, ids, tit


# ------------------------------------------------------------------ historico em disco
def _dia(ts):
    return datetime.fromtimestamp(ts, BRT).strftime("%Y-%m-%d")


def _indexar(chave, rec):
    """rec = [preco, ts, msg_id, "palavras", "ids", "titulo"]"""
    atual = _IDX["k"].get(chave)
    if atual and atual[0] <= rec[0]:
        return False
    _IDX["k"][chave] = rec
    for w in rec[3].split():
        _IDX["tok"].setdefault(w, set()).add(chave)
    return True


def carregar(dias=90):
    if _IDX["carregado"]:
        return
    _IDX["carregado"] = True
    try:
        with open(os.path.join(PASTA, "_ponteiro.json"), encoding="utf-8") as f:
            _PTR.update(json.load(f))
    except Exception:  # noqa
        pass
    limite = _dia(time.time() - dias * 86400)
    try:
        nomes = sorted(n for n in os.listdir(PASTA) if re.match(r"\d{4}-\d\d-\d\d\.json$", n))
    except Exception:  # noqa
        nomes = []
    for n in nomes:
        if n[:10] < limite:
            continue
        try:
            with open(os.path.join(PASTA, n), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:  # noqa
            continue
        _IDX["dias"][n[:10]] = d
        for chave, rec in d.items():
            _indexar(chave, rec)


def _guardar(chave, preco, ts, mid, ws, ids, tit):
    dia = _dia(ts)
    d = _IDX["dias"].setdefault(dia, {})
    ant = d.get(chave)
    if ant and ant[0] <= preco:
        return
    rec = [round(preco, 2), int(ts), int(mid), " ".join(sorted(ws)), " ".join(ids), (tit or "")[:90]]
    d[chave] = rec
    _IDX["sujo"].add(dia)
    _indexar(chave, rec)


def salvar(dias=90):
    """Grava so os dias que mudaram (+ ponteiro) e apaga arquivos mais velhos que `dias`."""
    if not _IDX["carregado"]:
        return []
    os.makedirs(PASTA, exist_ok=True)
    feitos = []
    for dia in sorted(_IDX["sujo"]):
        caminho = os.path.join(PASTA, dia + ".json")
        tmp = caminho + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_IDX["dias"][dia], f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, caminho)
        feitos.append(caminho)
    _IDX["sujo"].clear()
    with open(os.path.join(PASTA, "_ponteiro.json"), "w", encoding="utf-8") as f:
        json.dump(_PTR, f, sort_keys=True)
    limite = _dia(time.time() - dias * 86400)
    for n in os.listdir(PASTA):
        if re.match(r"\d{4}-\d\d-\d\d\.json$", n) and n[:10] < limite:
            try:
                os.remove(os.path.join(PASTA, n))
            except Exception:  # noqa
                pass
    return feitos


def aprender(msgs):
    """msgs = [(id, texto, ts)] do SEU grupo."""
    n = 0
    for mid, txt, ts in msgs:
        r = registro(txt)
        if not r or not ts:
            continue
        chave, p, ws, ids, tit = r
        _guardar(chave, p, ts, mid, ws, ids, tit)
        n += 1
    return n


def atualizar(client, cfg, log, agora=None, max_novos=600, max_antigos=1500):
    """Le do SEU grupo o que entrou de novo e um pedaco do passado (ate `dias`). Limitado por ciclo."""
    pc = cfg.get("menor_preco") or {}
    grupo = (cfg.get("inteligencia") or {}).get("meu_grupo")
    if not grupo or not pc.get("ligado", True):
        return
    dias = int(pc.get("dias", 90))
    carregar(dias)
    agora = agora or time.time()
    grupo = int(grupo)
    if not _PTR.get("ult"):
        ult = [m for m in client.iter_messages(grupo, limit=1)]
        if not ult:
            return
        _PTR["ult"] = _PTR["back"] = ult[0].id + 1
        _PTR["fim"] = 0
        log("menor preço: começando a aprender o histórico do seu grupo (%d dias)" % dias)
    novos = []
    for m in client.iter_messages(grupo, min_id=int(_PTR["ult"]) - 1, limit=max_novos):
        if m.message and m.date:
            novos.append((m.id, m.message, m.date.timestamp()))
    if novos:
        aprender(novos)
        _PTR["ult"] = max(x[0] for x in novos) + 1
    if not _PTR.get("fim"):
        antigos, desde, mais_velho = [], agora - dias * 86400, None
        for m in client.iter_messages(grupo, offset_id=int(_PTR["back"]), limit=max_antigos):
            mais_velho = m.id
            if m.date and m.date.timestamp() < desde:
                _PTR["fim"] = 1
                break
            if m.message and m.date:
                antigos.append((m.id, m.message, m.date.timestamp()))
        if mais_velho is None:
            _PTR["fim"] = 1
        else:
            _PTR["back"] = mais_velho
        aprender(antigos)
        if _PTR.get("fim"):
            log("menor preço: histórico de %d dias aprendido (%d produtos)" % (dias, len(_IDX["k"])))
        elif antigos:
            log("menor preço: aprendendo o histórico... até %s (%d produtos)" % (
                datetime.fromtimestamp(antigos[-1][2], BRT).strftime("%d/%m"), len(_IDX["k"])))


# ------------------------------------------------------------------ comparacao
def menor_de(texto):
    """Devolve o registro do SEU menor preco para o produto do post (ou None)."""
    r = registro(texto)
    if not r:
        return None, None
    chave, p, ws, ids, tit = r
    candidatos = set()
    if chave in _IDX["k"]:
        candidatos.add(chave)
    for i in ids:
        if ("id:" + i) in _IDX["k"]:
            candidatos.add("id:" + i)
    cont = {}
    for w in ws:
        for k in _IDX["tok"].get(w, ()):
            cont[k] = cont.get(k, 0) + 1
    for k, c in cont.items():
        if c >= 3:
            rec = _IDX["k"].get(k)
            if rec:
                rids = set(rec[4].split()) if rec[4] else set()
                if ids and rids and not (set(ids) & rids):
                    continue                      # os dois tem codigo e os codigos sao diferentes
                if mesmo_produto(ws, set(rec[3].split())):
                    candidatos.add(k)
    melhor = None
    for k in candidatos:
        rec = _IDX["k"][k]
        if melhor is None or rec[0] < melhor[0]:
            melhor = rec
    return r, melhor


def _brl(v):
    s = "%.2f" % v
    inteiro, cent = s.split(".")
    inteiro = "{:,}".format(int(inteiro)).replace(",", ".")
    return "R$ %s,%s" % (inteiro, cent)


def _link_grupo(grupo, mid):
    g = str(grupo)
    g = g[4:] if g.startswith("-100") else g.lstrip("-")
    return "https://t.me/c/%s/%s" % (g, mid)


def _destino():
    """27/11: bot proprio de DICAS DE POSTAGEM (secret BOT_DICAS_TOKEN). Chat: CHAT_DICAS_ID ou, se faltar, o
    mesmo chat do monitora (CHAT_MONITOR_ID = o seu Telegram). Sem BOT_DICAS_TOKEN -> vai pro monitora."""
    tok = os.environ.get("BOT_DICAS_TOKEN", "")
    chat = os.environ.get("CHAT_DICAS_ID", "") or os.environ.get("CHAT_MONITOR_ID", "")
    if tok and chat:
        return tok, chat
    return os.environ.get("BOT_MONITOR_TOKEN", ""), os.environ.get("CHAT_MONITOR_ID", "")


def _enviar(token, chat, msg):
    body = json.dumps({"chat_id": chat, "text": msg, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=20)


def _trabalhar():
    from .cadastro_canais import _enviar_monitor, _log_fundo
    while True:
        msg, rotulo = _FILA.get()
        try:
            tok, chat = _destino()
            try:
                _enviar(tok, chat, msg)
                _log_fundo("  menor preço avisado: %s" % rotulo)
            except Exception as e:  # noqa
                if tok == os.environ.get("BOT_MONITOR_TOKEN", ""):
                    raise
                # bot de dicas falhou (ex.: ainda nao apertou /start nele): nao perde o aviso, manda no monitora
                _log_fundo("  aviso: bot de dicas falhou (%r); mandei no monitora" % e)
                _enviar_monitor(msg + chr(10) + chr(10) + "(o bot de dicas falhou: aperte /start nele)", log=_log_fundo)
        except Exception as e:  # noqa
            _log_fundo("  aviso: alerta de menor preço falhou (%s): %r" % (rotulo, e))
        finally:
            _FILA.task_done()


def conferir(posts_por_canal, cfg, est, log=print, agora=None):
    """Compara os posts novos com o seu historico e enfileira os avisos. Volta na hora."""
    pc = cfg.get("menor_preco") or {}
    if not pc.get("ligado", True) or not _IDX["k"]:
        return 0
    if not all(_destino()):
        return 0
    t = _THREAD["t"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_trabalhar, name="alerta_menor_preco", daemon=True)
        t.start()
        _THREAD["t"] = t
    agora = agora or time.time()
    grupo = (cfg.get("inteligencia") or {}).get("meu_grupo")
    n = 0
    for canal, posts in (posts_por_canal or {}).items():
        for p in posts:
            pid, txt = p[0], p[1]
            quando = p[2] if len(p) > 2 else None
            chave_post = "menor:%s:%s" % (canal, pid)
            if chave_post in _VISTOS:          # so em memoria: os ponteiros dos canais ja impedem reler
                continue
            _VISTOS.add(chave_post)
            if quando and agora - quando > 3 * 3600:
                continue
            r, meu = menor_de(txt)
            if not r or not meu:
                continue
            chave, preco, _, _, tit = r
            if preco > meu[0] + 0.005:
                continue
            igual = preco >= meu[0] * 0.99 - 0.005          # diferenca de menos de 1% conta como igual (centavos nao sao novidade)
            if igual and agora - meu[1] < 24 * 3600:
                continue                                  # voce acabou de postar por esse preco
            ja = _AVISADOS.get(meu[5]) or _AVISADOS.get(chave)
            if ja and agora - ja[1] < 12 * 3600 and preco >= ja[0] - 0.005:
                continue                                  # ja avisei esse produto por esse preco (ou menor)
            _AVISADOS[chave] = _AVISADOS[meu[5]] = (preco, agora)
            quando_meu = datetime.fromtimestamp(meu[1], BRT).strftime("%d/%m/%Y")
            from .cadastro_canais import _esc
            if igual:
                cab = "\U0001F7F0 <b>IGUAL ao seu menor preço</b>"
            else:
                cab = "\U0001F4C9 <b>ABAIXO do seu menor preço</b> (%d%% menor)" % round((1 - preco / meu[0]) * 100)
            aviso = ""
            if preco < meu[0] * 0.5:
                aviso = chr(10) + "⚠️ Mais de 50% abaixo: confira se é o mesmo produto/variação."
            msg = (cab + chr(10) + chr(10) +
                   "<b>%s</b>" % _esc(tit[:120]) + chr(10) +
                   "Agora: <b>%s</b> · canal %s" % (_brl(preco), _esc(canal)) + chr(10) +
                   "Seu menor: %s em %s" % (_brl(meu[0]), quando_meu) + chr(10) +
                   "Seu título: %s" % _esc(meu[5][:80]) + aviso + chr(10) + chr(10) +
                   "Post do canal: https://t.me/%s/%s" % (canal, pid) + chr(10) +
                   "Seu post: %s" % _link_grupo(grupo, meu[2]))
            try:
                _FILA.put_nowait((msg, "%s:%s" % (canal, pid)))
                n += 1
            except queue.Full:
                log("  aviso: fila do alerta de menor preço cheia")
    if len(_AVISADOS) > 5000:
        _AVISADOS.clear()
    if len(_VISTOS) > 20000:
        _VISTOS.clear()
    return n
