"""Leitura e limpeza do texto das promoções copiadas."""
import re
import unicodedata

URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
PRECO_RE = re.compile(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*|[0-9]+)(?:,([0-9]{1,2}))?", re.I)
CUPOM_RE = re.compile(r"(?:cupom|c[oó]digo|use o cupom|use)\s*[:\-]?\s*[\"“']?([A-Z0-9][A-Z0-9_\-]{2,24})[\"”']?", re.I)


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def norm(s):
    return sem_acento(s).lower()


def limpar(texto, cfg):
    """Remove 'Dica ...', '-Anúncio' e afins; mantém o resto fiel ao original."""
    prefixos = [norm(p) for p in cfg.get("remover_linhas_que_comecam", [])]
    out = []
    for linha in (texto or "").splitlines():
        n = norm(linha).strip().lstrip("-–—•* ").strip()
        if any(n.startswith(p.lstrip("-")) for p in prefixos):
            continue
        if re.match(r"^\W*dica\b", n):
            continue
        out.append(linha.rstrip())
    r = "\n".join(out)
    r = re.sub(r"\n{3,}", "\n\n", r).strip()
    return r


def links(texto):
    return [u.rstrip(".,;") for u in URL_RE.findall(texto or "")]


def cupom(texto):
    """Devolve o código de cupom (maiúsculo) ou None. Exige a palavra cupom/código perto."""
    for linha in (texto or "").splitlines():
        n = norm(linha)
        if ("cupom" in n or "codigo" in n) and "sem cupom" not in n and "nao precisa" not in n:
            m = re.search(r"(?:cupom|codigo)[^A-Za-z0-9]{0,20}([A-Za-z0-9][A-Za-z0-9_\-]{2,24})", sem_acento(linha), re.I)
            if m:
                c = m.group(1)
                if norm(c) in ("de", "para", "com", "off", "desconto", "aplicado", "ativo", "no", "na", "do", "da", "valido", "ate"):
                    continue
                if not (c.isupper() or any(ch.isdigit() for ch in c)):
                    continue  # código de cupom vem em MAIÚSCULAS ou com número
                return c.upper()
    return None


def _para_float(inteiro, cent):
    return float(inteiro.replace(".", "") + "." + (cent or "0").ljust(2, "0")[:2])


def preco(texto):
    """Preço principal: o menor preço 'por/à vista/pix' se houver; senão o primeiro R$.
    Retorna (float|None, texto_original_do_preco|None, tinha_centavos: bool)."""
    achados = []
    for m in PRECO_RE.finditer(texto or ""):
        ctx = norm(texto[max(0, m.start() - 25):m.start()])
        if any(p in ctx for p in ("de r$", "era", "antes", "frete", "parcel", "x de", "12x", "10x", "6x", "3x")):
            if "x de" in ctx or "parcel" in ctx or re.search(r"\d+x", ctx):
                continue
        achados.append((_para_float(m.group(1), m.group(2)), m.group(0), m.group(2) is not None, ctx))
    if not achados:
        return None, None, False
    preferidos = [a for a in achados if any(k in a[3] for k in ("por", "pix", "a vista", "à vista", "apenas", "so "))]
    escolhido = (preferidos or achados)[0]
    return escolhido[0], escolhido[1], escolhido[2]


def formatar_preco(valor, cent_padrao="99"):
    """315 -> '315,99'; 36.5 -> '36,50'; 1299 -> '1299,99' (sem ponto de milhar, como o bot espera)."""
    inteiro = int(valor)
    cents = round((valor - inteiro) * 100)
    if cents == 0:
        return "%d,%s" % (inteiro, cent_padrao)
    return "%d,%02d" % (inteiro, cents)
