"""Prepara o link para o Divulgador (ele converte para o afiliado do usuário)."""
import re
from urllib.parse import urlparse

ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", re.I)


def preparar(url, loja):
    """Amazon: link limpo /dp/ASIN SEM tag de terceiros. Outras lojas: como veio."""
    if loja == "amazon":
        m = ASIN_RE.search(url)
        if m:
            return "https://www.amazon.com.br/dp/%s" % m.group(1).upper()
        return url  # encurtado (amzn.to): o Divulgador resolve
    if loja == "mercadolivre":
        return url.split("#")[0]
    return url
