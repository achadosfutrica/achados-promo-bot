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
                       r"irgwc|sharedid|clickid|click_id|tracking.*|zanpid|xcod|lomadee.*|aw_.*|sv1|sv_.*|awinaffid|awinmid|ranlinkid|utm|gad_.*|srsltid)$", re.I)

# encurtadores e redes de afiliado (precisam ser abertos para saber a loja)
ENCURTADORES = ("bit.ly", "tinyurl.com", "cutt.ly", "tidd.ly", "awin1.com", "awin.com", "linksynergy.com",
                "click.linksynergy", "afilio.com.br", "rdir.", "lmd.re", "is.gd", "ow.ly", "t.co", "rebrand.ly",
                "abre.ai", "encurtador", "l.wl.co", "shorturl.at", "go.", "s.", "app.", "onelink.me", "linktr.ee",
                "divulgador.link", "di.link", "promo.", "clk.", "trk.", "pincei.co", "pincei.com.br", "link.amazon")


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


class _SemRedirecionar(__import__("urllib.request").request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None          # não segue: a gente lê o Location e decide


def resolver(url, timeout=10, voltas=6):
    """Abre encurtadores/links de afiliado SEM entrar na loja (lojas bloqueiam robôs):
    segue os 'Location' um a um e para assim que aparecer o link de uma loja conhecida."""
    import urllib.error
    import urllib.request
    abridor = urllib.request.build_opener(_SemRedirecionar())
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
    atual, achada = url, None
    for _ in range(voltas):
        alvo = desembrulhar(atual)
        if loja(alvo):
            if not rastreador(alvo):
                return alvo
            achada = alvo          # click.centauro.com.br/xyz: é da loja, mas ainda é um link curto -> segue
        try:
            r = abridor.open(urllib.request.Request(atual, headers=ua), timeout=timeout)
            loc, corpo = None, r.read(300000).decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if 300 <= e.code < 400 else None
            corpo = ""
            if not loc:
                return achada or url
        except Exception:  # noqa
            return achada or url
        if loc:
            from urllib.parse import urljoin
            atual = urljoin(atual, loc)
            continue
        # 200: página intermediária. Pincei e parecidos levam ao Mercado Livre: pega o código MLB do produto
        mlb = re.search(r"\bMLB-?(\d{6,})", corpo)
        if mlb and "mercadoli" in corpo.lower():
            return "https://produto.mercadolivre.com.br/MLB-%s" % mlb.group(1)
        # redirecionamento por HTML/JS (meta refresh, window.location) ou link de loja na página
        achou = None
        for m in re.finditer(r"https?:[\\/]+[^\"'<>\s]+", corpo):
            u = unquote(m.group(0).replace("\\/", "/"))
            if loja(desembrulhar(u)):
                return desembrulhar(u)
            if achou is None and host(u) and host(u) != host(atual) and parece_encurtado(u):
                achou = u
        if not achou:
            return achada or url
        atual = achou
    return achada or url


_PREFIXO_RASTREIO = ("click.", "clk.", "l.", "link.", "links.", "go.", "track.", "trk.", "r.", "t.", "tracking.", "redir.")


def rastreador(url):
    """Subdomínio de rastreio da própria loja com caminho curto (ex.: click.centauro.com.br/13Nl/3022r95u)."""
    h = host(url)
    if not h.startswith(_PREFIXO_RASTREIO):
        return False
    caminho = urlparse(url).path.strip("/")
    return len(caminho) <= 30 and caminho.count("/") <= 2 and "." not in caminho
