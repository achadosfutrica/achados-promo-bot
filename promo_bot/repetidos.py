"""Evita postar o MESMO produto 2x no mesmo dia (24 h), a não ser que o preço tenha caído bastante.

Compara com:
  - o que o robô já gerou (estado), e
  - o que saiu no SEU grupo nas últimas 24 h (inclui o que veio do Monitorar Grupos do Divulgador).
Mesmo produto = mesmo código (ASIN / MLB / Shopee) OU título muito parecido (>= 75% das palavras iguais).
"""
import re
import time

from . import inteligencia as IA
from . import texto as T

_IDS = [
    re.compile(r"(?:/dp/|/gp/product/|/gp/aw/d/)([A-Z0-9]{10})", re.I),       # Amazon ASIN
    re.compile(r"\bMLB-?(\d{6,})", re.I),                                      # Mercado Livre
    re.compile(r"i\.(\d+)\.(\d{6,})"),                                         # Shopee i.loja.item
    re.compile(r"shopee\.com\.br/product/(\d+)/(\d+)"),
    re.compile(r"magazineluiza\.com\.br/.*/p/([a-z0-9]{6,})/", re.I),
]


def ids(texto_ou_link):
    out = set()
    for rx in _IDS:
        for m in rx.finditer(texto_ou_link or ""):
            out.add("-".join(g.upper() for g in m.groups()))
    return out


def palavras(texto):
    return set(IA.palavras(IA.titulo(texto)))


def parecido(a, b, minimo=0.75):
    if len(a) < 3 or len(b) < 3:
        return False
    return len(a & b) / max(1, min(len(a), len(b))) >= minimo and len(a & b) / max(len(a | b), 1) >= 0.5


def registro(texto, link="", preco=None, t=None):
    return {"t": int(t or time.time()), "ids": sorted(ids((texto or "") + " " + (link or ""))),
            "w": sorted(palavras(texto)), "p": preco}


def ja_postado(reg, historico, desconto_min=0.15, horas=24, agora=None):
    """Devolve (True, motivo) se o produto já saiu nas últimas `horas` e o preço não caiu >= desconto_min."""
    agora = agora or time.time()
    w, i = set(reg["w"]), set(reg["ids"])
    for h in historico:
        if agora - h.get("t", 0) > horas * 3600:
            continue
        mesmo = (i and i & set(h.get("ids") or [])) or parecido(w, set(h.get("w") or []))
        if not mesmo:
            continue
        p_novo, p_ant = reg.get("p"), h.get("p")
        if p_novo and p_ant and p_novo <= p_ant * (1 - desconto_min):
            continue                    # caiu bastante: pode postar de novo
        return True, "ja postado hoje (antes R$ %s, agora R$ %s)" % (p_ant, p_novo)
    return False, ""


def limpar_historico(hist, horas=26, agora=None):
    agora = agora or time.time()
    return [h for h in hist if agora - h.get("t", 0) <= horas * 3600][-3000:]
