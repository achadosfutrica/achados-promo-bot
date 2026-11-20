"""Outras lojas (além de Amazon/ML/Shopee/Magalu): reconhecidas pelo DOMÍNIO do link.

O link vai para o bot do Divulgador, que converte para o seu afiliado (Awin, Rakuten, Afilio ou direto).
Links de afiliado de terceiros (awin1, linksynergy, encurtadores) são abertos para achar o produto.
"""
import re
from urllib.parse import parse_qs, unquote, urlparse, urlencode, urlunparse

# loja -> domínios (o host termina com um destes)
DOMINIOS = {
    "belezanaweb": ("belezanaweb.com.br",),
    "cea": ("cea.com.br",),
    "natura": ("natura.com.br",),
    "paguemenos": ("paguemenos.com.br",),
    "casasbahia": ("casasbahia.com.br",),
    "webcontinental": ("webcontinental.com.br",),
    "nespresso": ("nespresso.com",),
    "amobeleza": ("amobeleza.com.br",),
    "dafiti": ("dafiti.com.br",),
    "adidas": ("adidas.com.br",),
    "carrefour": ("carrefour.com.br",),
    "artwalk": ("artwalk.com.br",),
    "asics": ("asics.com.br",),
    "authenticfeet": ("authenticfeet.com.br",),
    "puma": ("puma.com",),
    "brastemp": ("brastemp.com.br",),
    "drogal": ("drogal.com.br",),
    "pacheco": ("drogariaspacheco.com.br",),
    "drogariasaopaulo": ("drogariasaopaulo.com.br",),
    "drogaraia": ("drogaraia.com.br",),
    "drogasil": ("drogasil.com.br",),
    "efacil": ("efacil.com.br",),
    "hering": ("hering.com.br", "espacohering.com.br"),
    "fastshop": ("fastshop.com.br",),
    "kabum": ("kabum.com.br",),
    "lg": ("lg.com",),
    "mizuno": ("mizuno.com.br",),
    "olympikus": ("olympikus.com.br",),
    "stanley": ("stanley1913.com.br", "stanley1913.com", "stanley.com.br"),
    "vivara": ("vivara.com.br",),
    # pedidas antes
    "nike": ("nike.com.br", "nike.com"),
    "netshoes": ("netshoes.com.br",),
    "centauro": ("centauro.com.br",),
    "epocacosmeticos": ("epocacosmeticos.com.br",),
    "zzmall": ("zzmall.com.br",),
    "futfanatics": ("futfanatics.com.br",),
}

# links de afiliado/redirecionamento: o produto vem num parâmetro
_PARAM_DESTINO = ("ued", "murl", "url", "u", "redirect", "redirect_url", "destination", "dest", "link", "deeplink", "r")

# parâmetros de rastreio de OUTROS afiliados (removidos; o Divulgador põe os seus)
_RASTREIO = re.compile(r"^(utm_.*|awc|ranmid|raneaid|ransiteid|siteid|clickref|afl|aff.*|partner.*|sc_.*|gclid|fbclid|"
                       r"cm_mmc|lmdsid|origem|source|campaign|influ|ref|tag|vtex_.*|mc_.*|_ga|mkt.*|cid|pid|pub.*|irclickid|"
                       r"irgwc|sharedid|clickid|click_id|tracking.*|zanpid|xcod|lomadee.*)$", re.I)

# encurtadores e redes de afiliado (precisam ser abertos para saber a loja)
ENCURTADORES = ("bit.ly", "tinyurl.com", "cutt.ly", "tidd.ly", "awin1.com", "awin.com", "linksynergy.com",
                "click.linksynergy", "afilio.com.br", "rdir.", "lmd.re", "is.gd", "ow.ly", "t.co", "rebrand.ly",
                "abre.ai", "encurtador", "l.wl.co", "shorturl.at", "go.", "s.", "app.", "onelink.me", "linktr.ee",
                "divulgador.link", "di.link", "promo.", "clk.", "trk.")


def host(url):
    try:
        h = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return h.lower()


def loja(url):
    h = host(url)
    if not h:
        return None
    for nome, doms in DOMINIOS.items():
        if any(h == d or h.endswith("." + d) for d in doms):
            return nome
    return None


def desembrulhar(url, voltas=3):
    """awin1.com/cread.php?...&ued=<produto>  ->  <produto> (sem rede de terceiros)."""
    for _ in range(voltas):
        if loja(url):
            return url
        try:
            q = parse_qs(urlparse(url).query)
        except ValueError:
            return url
        prox = None
        for k, vs in q.items():
            if k.lower() in _PARAM_DESTINO:
                for v in vs:
                    v = unquote(v)
                    if v.startswith("http"):
                        prox = v
                        break
            if prox:
                break
        if not prox:
            return url
        url = prox
    return url


def limpar(url):
    """Tira rastreio de terceiros e o #fragmento; mantém o resto (ex.: ?skuId=)."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    q = [(k, v) for k, vs in parse_qs(p.query, keep_blank_values=True).items() for v in vs if not _RASTREIO.match(k)]
    return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, urlencode(q, doseq=True), ""))


def parece_encurtado(url):
    h = host(url)
    if not h:
        return False
    if loja(url):
        return False
    caminho = urlparse(url).path.strip("/")
    curto = len(caminho) <= 20 and "/" not in caminho
    return any(e in h for e in ENCURTADORES) or (curto and h.count(".") <= 2 and len(h) <= 14)


def resolver(url, timeout=10):
    """Segue os redirecionamentos (só para encurtadores) e devolve a URL final. Sem rede -> a própria URL."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
            if loja(final):
                return final
            # alguns redirecionam por HTML/JS: procura um link de loja na página
            corpo = r.read(300000).decode("utf-8", "ignore")
            for m in re.finditer(r"https?:[\\/]+[^\"'<>\s]+", corpo):
                u = m.group(0).replace("\\/", "/")
                u = desembrulhar(unquote(u))
                if loja(u):
                    return u
            return final
    except Exception:  # noqa
        return url
