"""Mercado Livre: descobre o "Vendido por <loja>" e monta a informação extra do post.

Ordem: (1) o próprio texto do post; (2) a página do produto; (3) a API pública do ML.
Qualquer falha devolve None (o post sai sem a informação extra, nunca trava).
"""
import html
import json
import re
import urllib.request

from . import texto as T

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
ITEM_RE = re.compile(r"\b(MLB-?\d{6,12})\b", re.I)

MODELO_EXTRA = (
    '📌 Vendido por Loja "{loja}" no ML - Loja confiável/perfume original\n\n'
    '📢 Se não abrir na loja certa, clique em "outras opções de compra" e role até encontrar a loja'
)

_RUIDO = re.compile(r"^(mercado ?livre|loja oficial|vendido por|ver mais|\+?\d|mais vendid|mercadolider|\W*$)", re.I)


def _limpar_nome(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip(" .:-|\"'“”")
    s = re.sub(r"\s*(\+?\d+(?:[.,]\d+)?\s*(mil)?\s*vendas?.*)$", "", s, flags=re.I)
    s = re.sub(r"^loja oficial\s+", "", s, flags=re.I).strip()
    if not s or len(s) > 60 or _RUIDO.match(s):
        return None
    return s


def do_texto(texto):
    """'Vendido por: LOJA X' escrito no próprio post."""
    for linha in (texto or "").splitlines():
        m = re.search(r"vendid[oa]\s+(?:e\s+entregue\s+)?por\s*:?\s*(.+)$", T.sem_acento(linha), re.I)
        if m:
            ini = len(T.sem_acento(linha)) - len(m.group(1))
            nome = _limpar_nome(linha[ini:])
            if nome:
                return nome
    return None


def do_html(pagina):
    """Lê o vendedor do HTML do produto do ML."""
    if not pagina:
        return None
    for pad in (r'"seller_name"\s*:\s*"([^"]{2,80})"',
                r'"sellerName"\s*:\s*"([^"]{2,80})"',
                r'"official_store_name"\s*:\s*"([^"]{2,80})"'):
        m = re.search(pad, pagina)
        if m:
            nome = _limpar_nome(json.loads('"%s"' % m.group(1)) if "\\u" in m.group(1) else m.group(1))
            if nome:
                return nome
    i = pagina.find("Vendido por")
    while i >= 0:
        trecho = pagina[i + len("Vendido por"):i + 900]
        for txt in re.findall(r">([^<>]{2,80})<", ">" + trecho):
            nome = _limpar_nome(txt)
            if nome:
                return nome
        i = pagina.find("Vendido por", i + 10)
    return None


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read(2_500_000).decode("utf-8", "replace")


def da_api(item_id, get=_get):
    try:
        _, j = get("https://api.mercadolibre.com/items/%s" % item_id.replace("-", "").upper())
        sid = json.loads(j).get("seller_id")
        if not sid:
            return None
        _, u = get("https://api.mercadolibre.com/users/%s" % sid)
        return _limpar_nome(json.loads(u).get("nickname", ""))
    except Exception:  # noqa
        return None


def vendedor(link, texto="", get=_get, log=lambda s: None):
    nome = do_texto(texto)
    if nome:
        return nome
    final, pagina = link, ""
    try:
        final, pagina = get(link)
    except Exception as e:  # noqa
        log("  ML vendedor: nao abri a pagina (%s)" % e)
    nome = do_html(pagina)
    if nome:
        return nome
    m = ITEM_RE.search(final or "") or ITEM_RE.search(pagina or "") or ITEM_RE.search(link or "")
    if m:
        return da_api(m.group(1), get)
    return None


def extra(link, texto="", get=_get, log=lambda s: None):
    nome = vendedor(link, texto, get, log)
    return MODELO_EXTRA.format(loja=nome) if nome else None
