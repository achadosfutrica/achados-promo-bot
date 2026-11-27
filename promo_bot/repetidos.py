"""Evita postar o MESMO produto 2x no mesmo dia, a não ser que o preço tenha caído bastante.

Queda mínima exigida para repostar (regra do Oscar, 25/11/2026), pela faixa do preço NOVO:
  até R$ 100 -> 20%  |  até R$ 500 -> 15%  |  até R$ 1.000 -> 12%  |  até R$ 5.000 -> 10%
  até R$ 10.000 -> 5%  |  acima de R$ 10.000 -> 5%

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


def inicio_do_dia(agora):
    """00:01 (Brasilia) de hoje: so o que saiu de hoje em diante conta como repetido; o de ontem pode repetir.
    EXCECAO so 27/11/2026 (pedido do Oscar so por hoje): corta as 04:00 em vez de 00:01, liberando repostar
    o que saiu entre 22:00 de ontem e 04:00 de hoje. Se autorrevogar sozinha (so vale nessa data)."""
    from datetime import datetime, timedelta, timezone
    brt = timezone(timedelta(hours=-3))
    hoje = datetime.fromtimestamp(agora, brt)
    if hoje.strftime("%Y-%m-%d") == "2026-11-27":
        d = hoje.replace(hour=4, minute=0, second=0, microsecond=0)
    else:
        d = hoje.replace(hour=0, minute=1, second=0, microsecond=0)
    return d.timestamp()


FAIXAS = [(100, 0.20), (500, 0.15), (1000, 0.12), (5000, 0.10), (10000, 0.05)]
ACIMA = 0.05


def desconto_exigido(preco, faixas=None, acima=ACIMA):
    """Queda mínima (0.20 = 20%) para repostar um produto que agora custa `preco` (faixa pelo preço novo)."""
    for teto, d in sorted((float(a), float(b)) for a, b in (faixas or FAIXAS)):
        if preco <= teto:
            return d
    return float(acima)


def ja_postado(reg, historico, faixas=None, horas=24, agora=None, acima=ACIMA):
    """Devolve (True, motivo) se o MESMO produto já saiu hoje e o preço não caiu o mínimo da faixa."""
    agora = agora or time.time()
    if isinstance(faixas, (int, float)) and not isinstance(faixas, bool):
        faixas = [(float("inf"), float(faixas))]        # config/main antigos: um desconto fixo para todos os precos
    w, i = set(reg["w"]), set(reg["ids"])
    for h in historico:
        if h.get("t", 0) < inicio_do_dia(agora):
            continue
        mesmo = (i and i & set(h.get("ids") or [])) or parecido(w, set(h.get("w") or []))
        if not mesmo:
            continue
        p_novo, p_ant = reg.get("p"), h.get("p")
        if p_novo and p_ant:
            exigido = desconto_exigido(p_novo, faixas, acima)
            if p_novo <= p_ant * (1 - exigido) + 0.005:
                continue                # caiu o suficiente para a faixa: pode postar de novo
            return True, "ja postado hoje (antes R$ %s, agora R$ %s: precisa cair %d%% nessa faixa)" % (p_ant, p_novo, round(exigido * 100))
        return True, "ja postado hoje (antes R$ %s, agora R$ %s: sem preco para comparar)" % (p_ant, p_novo)
    return False, ""


def limpar_historico(hist, horas=26, agora=None):
    agora = agora or time.time()
    return [h for h in hist if agora - h.get("t", 0) <= horas * 3600][-3000:]
