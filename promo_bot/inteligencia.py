"""Inteligência: dá uma NOTA para cada promoção dos canais com base no que VOCÊ costuma postar.

Perfil (refeito 1x por dia): lê os seus próprios posts no seu grupo do Telegram (o Divulgador posta lá)
dos últimos dias e conta as palavras dos títulos (marcas, tipos de produto) e as faixas de preço.

Nota de um post (0 a ~100):
  + até 40  parecido com o que você posta (palavras do título)
  + até 10  faixa de preço que você costuma postar
  + até 40  desconto (De R$ X por R$ Y); 60% ou mais = BUG (+20 extra)
  + 5       tem cupom
  + bônus   lojas novas (config bonus_loja), para não ficar só Amazon/ML
"""
import math
import re
import time

from . import texto as T

STOP = set("""
de da do das dos para por com sem em no na nos nas um uma uns umas o a os as e ou que se ao aos mais menos
kit cor tam tamanho original novo nova oferta promo promocao promocoes cupom desconto pix vista frete gratis
loja oficial link compre aqui hoje agora apenas so preco valor reais off ate acima unidade unidades und pack
""".split())

FAIXAS = [0, 30, 60, 100, 200, 400, 800, 1500, 3000, 10 ** 9]


def _gatilho(s):
    """Linha de chamada ('LEVE TRÊS PELO PREÇO DE UMA!!', 'CORRE!!') e não nome de produto."""
    letras = [c for c in s if c.isalpha()]
    if not letras:
        return True
    maius = sum(c.isupper() for c in letras) / len(letras)
    return maius > 0.6 or s.rstrip().endswith("!!")


def titulo(texto):
    """Nome do produto: 1ª linha que não é link, preço, cupom nem chamada em MAIÚSCULAS."""
    candidatas = []
    for l in (texto or "").splitlines():
        s = T.URL_RE.sub("", l).strip()
        n = T.norm(s)
        if len(re.sub(r"\W", "", s)) < 4 or T.PRECO_RE.search(s) or re.match(r"^\W*(cupom|codigo|link|compre)\b", n):
            continue
        candidatas.append(s)
    for s in candidatas:
        if not _gatilho(s):
            return s
    return candidatas[0] if candidatas else ""


def palavras(s):
    out = []
    for w in re.findall(r"[a-z0-9]+", T.norm(s)):
        if len(w) < 3 or w in STOP or w.isdigit():
            continue
        out.append(w)
    return out


def faixa(p):
    for i in range(len(FAIXAS) - 1):
        if FAIXAS[i] <= p < FAIXAS[i + 1]:
            return i
    return len(FAIXAS) - 2


def montar_perfil(textos, agora=None):
    """textos: posts SEUS (texto completo). Devolve dict guardável no estado."""
    cont, faixas, n = {}, [0] * (len(FAIXAS) - 1), 0
    for t in textos:
        ws = set(palavras(titulo(t)))
        if not ws:
            continue
        n += 1
        for w in ws:
            cont[w] = cont.get(w, 0) + 1
        p, _, _ = T.preco(t)
        if p:
            faixas[faixa(p)] += 1
    top = dict(sorted(cont.items(), key=lambda x: -x[1])[:3000])
    return {"t": int(agora or time.time()), "n": n, "palavras": top, "faixas": faixas}


def perfil_vencido(perfil, horas=24, agora=None):
    return not perfil or not perfil.get("n") or (agora or time.time()) - perfil.get("t", 0) > horas * 3600


_ANTIGO = re.compile(r"(?:(?<!acima )(?<!partir )(?<!compras )(?<!minimo )(?<!mínimo )\bde|\bera|\bantes)\s*:?\s*~?\s*R\$\s*([0-9]{1,3}(?:\.[0-9]{3})+(?![0-9])|[0-9]+)(?:,([0-9]{1,2}))?(?![0-9])|~\s*R\$\s*([0-9]{1,3}(?:\.[0-9]{3})+(?![0-9])|[0-9]+)(?:,([0-9]{1,2}))?(?![0-9])", re.I)


def preco_antigo(texto):
    vals = []
    for m in _ANTIGO.finditer(texto or ""):
        inteiro, cent = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        vals.append(float(inteiro.replace(".", "") + "." + (cent or "0").ljust(2, "0")[:2]))
    return max(vals) if vals else None


def desconto(texto, preco):
    """% de desconto (0..1) do post, se ele trouxer o preço antigo ou '% OFF'."""
    de = preco_antigo(texto)
    if de and preco and de > preco:
        return 1 - preco / de
    m = re.search(r"(\d{1,2})\s*%\s*(?:de\s+)?(?:off|desc)", T.norm(texto or ""))
    if m and "cupom" not in T.norm(texto)[max(0, m.start() - 25):m.start()]:
        return int(m.group(1)) / 100
    return 0.0


def nota(texto, info, perfil, cfg):
    """Devolve (nota, detalhes:str)."""
    ic = cfg.get("inteligencia") or {}
    det = []
    total = 0.0
    # parecido com o que você posta
    if perfil and perfil.get("palavras"):
        pal = perfil["palavras"]
        mx = max(pal.values()) or 1
        ws = palavras(titulo(texto))
        if ws:
            pesos = sorted((math.log1p(pal.get(w, 0)) / math.log1p(mx) for w in ws), reverse=True)[:4]
            sim = sum(pesos) / 4
            total += 40 * sim
            det.append("perfil %.0f" % (40 * sim))
        fx = perfil.get("faixas") or []
        if fx and sum(fx) and info.get("preco"):
            share = fx[faixa(info["preco"])] / max(fx)
            total += 10 * share
            det.append("preco %.0f" % (10 * share))
    # desconto / bug
    d = desconto(texto, info.get("preco"))
    if d:
        pts = 50 * min(d, 0.8)
        total += pts
        det.append("desc %d%%" % round(d * 100))
        if d >= ic.get("bug_desconto", 0.6):
            total += 20
            det.append("BUG")
    if info.get("cupom"):
        total += 5
        det.append("cupom")
    bonus = (cfg.get("bonus_loja") or {})
    b = bonus.get(info.get("loja"), bonus.get("_novas", 0) if info.get("loja") not in ("amazon", "mercadolivre", "shopee", "magalu") else 0)
    if b:
        total += b
        det.append("loja +%s" % b)
    return round(total, 1), ", ".join(det)
