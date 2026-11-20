"""Decide se um post é uma promoção que deve ser copiada."""
import re
from . import lojas_extra as LX
from . import texto as T

LOJAS = {
    "amazon": ("amazon.com", "amzn.to", "amzn.com", "a.co/"),
    "mercadolivre": ("mercadolivre.com", "mercadolibre.com", "meli.la", "mlb.", "mercadolivre.com.br"),
    "shopee": ("shopee.com", "s.shopee", "shp.ee"),
    "magalu": ("magazineluiza", "magalu", "magazinevoce"),
    "aliexpress": ("aliexpress", "s.click.aliexpress", "a.aliexpress"),
}


def loja_do_link(url):
    u = url.lower()
    for loja, pistas in LOJAS.items():
        if any(p in u for p in pistas):
            return loja
    return LX.loja(LX.desembrulhar(url))


def link_da_loja(url, loja):
    """Lojas extras: abre link de afiliado de terceiros e tira rastreio. As 4 principais: como veio."""
    if loja in LOJAS:
        return url
    return LX.limpar(LX.desembrulhar(url))


def eh_periferico_de_pc(texto, cfg):
    n = T.norm(texto)
    # frases de bloqueio comparadas por palavra inteira
    def tem(p):
        p = T.norm(p)
        return re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", n) is not None
    if any(tem(p) for p in cfg.get("liberar", [])):
        return False
    return any(tem(p) for p in cfg.get("bloquear", []))


def avaliar(texto, cfg, resolver=None):
    """Retorna (ok, motivo, info). info = {link, loja, preco, cupom}."""
    if not texto or not texto.strip():
        return False, "vazio", {}
    n = T.norm(texto)
    if cfg.get("bloquear_sacola_laranja", True) and T.tem_sacola_laranja(texto):
        return False, "sacola laranja (bloqueado)", {}
    if "aliexpress" in n or "ali express" in n:
        return False, "aliexpress (bloqueado)", {}
    for frase in cfg.get("bloquear_texto", []):
        if T.norm(frase) in n:
            return False, "texto bloqueado: " + frase, {}
    urls = T.links(texto)
    if not urls:
        return False, "sem link", {}
    lojas_ok = cfg["lojas"]
    escolhido = None
    for u in urls:
        loja = loja_do_link(u)
        if loja:
            escolhido = (u, loja)
            break
    if not escolhido and resolver:
        for u in urls:                       # encurtado / rede de afiliado: abre e vê qual loja é
            if LX.parece_encurtado(u):
                final = resolver(u)
                loja = loja_do_link(final) if final else None
                if loja:
                    escolhido = (final, loja)
                    break
    if not escolhido:
        return False, "loja desconhecida", {}
    link, loja = escolhido
    link = link_da_loja(link, loja)
    if not lojas_ok.get(loja, False):
        return False, "loja desativada: " + loja, {"loja": loja}
    p, _, _ = T.preco(texto)
    c = T.cupom(texto)
    if p is None and c:
        return False, "so cupom (ignorado)", {"loja": loja}
    if p is None:
        return False, "sem preco", {"loja": loja}
    if eh_periferico_de_pc(texto, cfg):
        return False, "periferico de PC", {"loja": loja}
    return True, "ok", {"link": link, "loja": loja, "preco": p, "cupom": c}
