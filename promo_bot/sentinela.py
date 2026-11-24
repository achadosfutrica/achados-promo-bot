"""Sentinela do valor mínimo de cupom (v74).

O Divulgador não tem campo de "compra mínima" no cupom: quando o monitoramento dele copia
"R$ 230 OFF acima de R$ 1.499: TODO2211SITE", ele desconta os R$ 230 de qualquer produto
(foi assim que o Campari de R$ 38 saiu por -R$ 192).

A sentinela:
  1) APRENDE o mínimo de cada cupom lendo os canais de cupom (juaocupons, promotom, fumotom...);
  2) VIGIA o grupo onde o Divulgador posta: post com preço zerado/negativo, ou com cupom cujo
     mínimo é maior que o preço, é APAGADO do grupo do Telegram e você recebe o aviso nas
     Mensagens Salvas com o link do produto para corrigir no Divulgador.
"""
import re
import time

from . import texto as T

_COD_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_\-]{3,24}\d[A-Z0-9_\-]*|[A-Z0-9_\-]*\d[A-Z0-9_\-]*[A-Z][A-Z0-9_\-]{2,24})(?![A-Za-z0-9_])")
_NAO_COD = {"R$", "OFF", "PIX", "APP", "FPS60", "FPS70", "USB", "HDMI", "LED", "QLED", "UHD", "FULL", "HD"}


def _num(s):
    s = str(s or "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def codigos_da_linha(linha):
    out = []
    for m in _COD_RE.finditer(T.sem_acento(linha or "").replace("`", " ")):
        c = m.group(1).strip("-_").upper()
        if len(c) < 4 or c in _NAO_COD or c in T._LOJAS_NOME or re.fullmatch(r"\d+[A-Z]{0,3}", c):
            continue
        if re.fullmatch(r"\d+(GB|TB|MB|ML|KG|G|L|V|W|K|MM|CM|HZ|MAH|BTUS?|X)", c):
            continue
        out.append(c)
    return out


def aprender(texto):
    """{CODIGO: minimo} de um post de cupom. Cada linha com desconto+mínimo vale para o(s) código(s)
    da mesma linha ou da linha seguinte (ex.: 'R$ 100 OFF em R$ 1100:' / 'INFLU100BLACK')."""
    linhas = [l.strip() for l in (texto or "").splitlines() if l.strip()]
    achados = {}
    for i, l in enumerate(linhas):
        det = T.cupom_detalhes(l)
        if not det or not det.get("minimo"):
            continue
        cods = codigos_da_linha(l) or (codigos_da_linha(linhas[i + 1]) if i + 1 < len(linhas) and not T.cupom_detalhes(linhas[i + 1]) else [])
        for c in cods:
            achados[c] = {"min": det["minimo"], "tipo": det["tipo"], "valor": det["valor"]}
    if not achados:                       # post de 1 cupom: "cupom X" numa linha e "acima de R$ 99" em outra
        det = T.cupom_detalhes(texto or "")
        c = T.cupom(texto or "")
        if det.get("minimo") and c:
            achados[c] = {"min": det["minimo"], "tipo": det["tipo"], "valor": det["valor"]}
    return achados


def preco_do_post(texto):
    """Preço final do post do Divulgador. Aceita '-R$ 192,00' (negativo)."""
    t = texto or ""
    for linha in t.splitlines():
        n = T.norm(linha)
        if "r$" not in n:
            continue
        if re.search(r"\bde\s*:?\s*(?:-\s*)?r\$", n) and not re.search(r"\bpor\b", n):
            continue                       # linha do preço antigo ("De R$ ...")
        m = re.search(r"(-)?\s*R\$\s*(-)?\s*([\d.]+(?:,\d{1,2})?)", linha, re.I)
        if m:
            v = _num(m.group(3))
            if v is None:
                continue
            return -v if (m.group(1) or m.group(2)) else v
    return None


def problema(texto, minimos):
    """Texto do problema do post ou None."""
    p = preco_do_post(texto)
    if p is None:
        return None
    cod = T.cupom(texto or "")
    if p <= 0:
        return "preço %s (zerado ou negativo%s)" % (("-R$ %s" % T.formatar_preco(-p)) if p < 0 else "R$ 0", ", cupom " + cod if cod else "")
    if cod and cod in minimos:
        info = minimos[cod]
        # o preço do post pode já estar com o desconto: estima o preço ANTES do cupom (assim não acusa à toa)
        if info.get("tipo") == "fixed" and info.get("valor"):
            antes = p + float(info["valor"])
        elif info.get("tipo") == "percent" and info.get("valor") and float(info["valor"]) < 100:
            antes = p / (1 - float(info["valor"]) / 100.0)
        else:
            antes = p
        if antes < info["min"] - 0.01:
            return "cupom %s só vale acima de R$ %s, e o produto custa R$ %s" % (cod, T.formatar_preco(info["min"]), T.formatar_preco(p))
    return None


def _exec(loop, x):
    """Roda no loop se for corrotina; se o cliente ja devolveu o resultado (telethon.sync), usa direto."""
    import inspect
    if inspect.isawaitable(x):
        return loop.run_until_complete(x)
    return x


class Sentinela:
    def __init__(self, client, cfg, log, canais, memoria=None):
        sc = cfg.get("sentinela") or {}
        self.c, self.log = client, log
        self.ligada = sc.get("ligado", True)
        self.apagar = sc.get("apagar_do_grupo", True)
        self.grupo = (cfg.get("inteligencia") or {}).get("meu_grupo")
        self.canais = [str(x).lstrip("@").lower() for x in (sc.get("canais_cupom") or canais)]
        # CODIGO -> {"min": 1499.0, "tipo": "fixed", "valor": 230.0, "t": epoch} (guardado no estado: vale 3 dias)
        self.minimos = memoria if memoria is not None else {}
        self.avisados = self.minimos.setdefault("__avisados", [])      # posts já avisados (não repete o aviso)
        for c in [c for c, v in self.minimos.items() if isinstance(v, dict) and time.time() - float(v.get("t") or 0) > 3 * 86400]:
            self.minimos.pop(c, None)
        del self.avisados[:-200]
        self.vistos = set()
        self._handlers = []

    def aprender(self, texto):
        for c, info in aprender(texto).items():
            info["t"] = time.time()
            if c not in self.minimos:
                self.log("sentinela: cupom %s minimo R$ %s" % (c, T.formatar_preco(info["min"])))
            self.minimos[c] = info

    async def conferir(self, msg):
        chave = (getattr(msg, "id", None), str(getattr(msg, "edit_date", "") or ""))
        if not msg or chave in self.vistos:
            return
        self.vistos.add(chave)
        txt = msg.message or ""
        prob = problema(txt, self.minimos)
        if not prob or msg.id in self.avisados:
            return
        self.avisados.append(msg.id)
        link = "https://t.me/c/%s/%s" % (str(self.grupo).replace("-100", "", 1), msg.id)
        site = re.search(r"https?://(?:www\.)?achadosfutrica\.com\.br/p/\S+", txt)
        apagou = False
        if self.apagar:
            try:
                await self.c.delete_messages(int(self.grupo), [msg.id])
                apagou = True
            except Exception as e:  # noqa
                self.log("aviso: sentinela: nao apaguei do grupo: %r" % e)
        nome = next((l.strip() for l in txt.splitlines() if len(l.strip()) > 8 and "R$" not in l), "")[:90]
        aviso = ("🚨 CUPOM ABAIXO DO MÍNIMO — postagem do Divulgador\n"
                 "%s\n⚠️ %s\n%s\n%s\n\n👉 Corrija no Divulgador (tire o cupom ou acerte o preço)%s."
                 % (nome, prob, ("🗑 Apaguei do grupo do Telegram." if apagou else "🔗 " + link),
                    ("🛒 " + site.group(0)) if site else "", " e confira o WhatsApp" if apagou else ""))
        self.log("sentinela: %s | %s" % (prob, nome))
        try:
            await self.c.send_message("me", aviso, link_preview=False)
        except Exception as e:  # noqa
            self.log("aviso: sentinela: nao avisei: %r" % e)

    async def _ev_canal(self, ev):
        try:
            self.aprender(ev.message.message or "")
        except Exception as e:  # noqa
            self.log("aviso: sentinela (canal): %r" % e)

    async def _ev_grupo(self, ev):
        try:
            await self.conferir(ev.message)
        except Exception as e:  # noqa
            self.log("aviso: sentinela (grupo): %r" % e)

    def ligar(self):
        if not self.ligada or not self.grupo:
            return
        from telethon import events
        loop = self.c.loop
        for canal in self.canais:          # aprende o que já está nos canais (últimos posts)
            try:
                msgs = _exec(loop, self.c.get_messages(canal, limit=60))
                for m in msgs or []:
                    if m and m.message:
                        self.aprender(m.message)
            except Exception as e:  # noqa
                self.log("aviso: sentinela: nao li %s: %r" % (canal, e))
        try:                                  # o que o Divulgador postou nos últimos minutos também é conferido
            for m in _exec(loop, self.c.get_messages(int(self.grupo), limit=30)) or []:
                _exec(loop, self.conferir(m))
        except Exception as e:  # noqa
            self.log("aviso: sentinela: nao li o grupo: %r" % e)
        h1, h2 = self._ev_canal, self._ev_grupo
        self.c.add_event_handler(h1, events.NewMessage(chats=self.canais))
        self.c.add_event_handler(h2, events.NewMessage(chats=[int(self.grupo)]))
        self.c.add_event_handler(h2, events.MessageEdited(chats=[int(self.grupo)]))
        self._handlers = [h1, h2]
        self.log("sentinela ligada: %d cupom(ns) com minimo conhecidos" % len(self.minimos))

    def desligar(self):
        for h in self._handlers:
            try:
                self.c.remove_event_handler(h)
            except Exception:  # noqa
                pass
