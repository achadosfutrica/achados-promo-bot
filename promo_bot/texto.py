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


_OLD_ANTES = re.compile(r"(?:\bde|\bera|\bantes|\bdo)\s*:?\s*$")


def _contexto(texto, m):
    """(antes, depois) normalizados, limitados à mesma linha."""
    ini = texto.rfind("\n", 0, m.start()) + 1
    fim = texto.find("\n", m.end())
    fim = len(texto) if fim < 0 else fim
    antes = norm(texto[max(ini, m.start() - 30):m.start()])
    depois = norm(texto[m.end():min(fim, m.end() + 30)])
    linha = norm(texto[ini:fim])
    return antes, depois, linha


def preco(texto):
    """Preço principal, SEMPRE o do Pix/à vista quando o post trouxer (Shopee, Amazon, ML...).
    Ordem: menor preço marcado com pix/à vista > primeiro 'por/apenas/só' > primeiro R$.
    Ignora parcelas (10x de R$), preço antigo (De R$ / ~R$~) e frete.
    Retorna (float|None, texto_original_do_preco|None, tinha_centavos: bool)."""
    texto = texto or ""
    pix, por, outros = [], [], []
    for m in PRECO_RE.finditer(texto):
        antes, depois, linha = _contexto(texto, m)
        if re.search(r"\d+\s*x\s*(de\s*)?$", antes) or "parcel" in antes or re.match(r"\s*(em|sem juros)", depois) and re.search(r"\d+\s*x", linha):
            continue  # parcela
        if "frete" in antes[-12:]:
            continue
        if _OLD_ANTES.search(antes) or texto[max(0, m.start() - 1):m.start()] == "~":
            continue  # preço antigo
        item = (_para_float(m.group(1), m.group(2)), m.group(0), m.group(2) is not None)
        if re.search(r"\bpix\b|a vista", antes[-14:] + " " + depois[:16]) or (re.search(r"\bpix\b|a vista", linha) and not re.search(r"\d+\s*x", linha)):
            pix.append(item)
        elif re.search(r"\b(por|apenas|so|sai|saindo|fica)\b\s*:?\s*$", antes):
            por.append(item)
        else:
            outros.append(item)
    if pix:
        return min(pix, key=lambda a: a[0])
    if por:
        return por[0]
    if outros:
        return outros[0]
    return None, None, False


def formatar_preco(valor, cent_padrao="99"):
    """315 -> '315,99'; 36.5 -> '36,50'; 1299 -> '1299,99' (sem ponto de milhar, como o bot espera)."""
    inteiro = int(valor)
    cents = round((valor - inteiro) * 100)
    if cents == 0:
        return "%d,%s" % (inteiro, cent_padrao)
    return "%d,%02d" % (inteiro, cents)


# ------------------------------------------------------------------
# Detalhes do cupom (para cadastrar no Divulgador antes de gerar)
# ------------------------------------------------------------------
def _num(s):
    s = (s or "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d{1,3}(\.\d{3})+$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def cupom_detalhes(texto):
    """Lê do post: desconto (% ou R$), compra mínima ('acima de') e desconto máximo.
    Retorna {} se não achar desconto. Ex.: {'tipo': 'percent', 'valor': 10, 'minimo': 99.0, 'maximo': 50.0}"""
    n = norm(texto or "")
    out = {}
    m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:de\s+)?(?:off|desc)", n) or re.search(r"desconto\s+de\s+(\d{1,2}(?:[.,]\d+)?)\s*%", n)
    if m:
        out = {"tipo": "percent", "valor": _num(m.group(1))}
    else:
        m = (re.search(r"r\$\s*([\d.]+(?:,\d{1,2})?)\s*(?:de\s+)?(?:off|desc)", n)
             or re.search(r"desconto\s+de\s+r\$\s*([\d.]+(?:,\d{1,2})?)", n)
             or re.search(r"(?<![\d,.])(\d+(?:,\d{1,2})?)\s*reais\s*(?:de\s+)?(?:off|desc)", n)
             or re.search(r"(?<![\w$,.])(\d{1,4})\s*off\b", n))
        if m:
            out = {"tipo": "fixed", "valor": _num(m.group(1))}
    if not out or not out["valor"]:
        return {}
    mi = re.search(r"(?:acima\s+de|a\s+partir\s+de|compras?\s+(?:de|acima de)|minim[oa](?:\s+de)?|pedido\s+minimo(?:\s+de)?)\s*:?\s*r\$\s*([\d.]+(?:,\d{1,2})?)", n) \
        or re.search(r"\boff\s+em\s+(?:r\$\s*)?([\d.]+(?:,\d{1,2})?)", n)
    if mi:
        v = _num(mi.group(1))
        if v and v > 1:
            out["minimo"] = v
    mx = re.search(r"(?:max(?:imo)?|limite(?:\s+de)?|limitad[oa]\s+a|ate)\s*(?:de\s+)?(?:desconto\s+)?:?\s*r\$\s*([\d.]+(?:,\d{1,2})?)\s*(?:de\s+desconto|off)?", n)
    if mx and out["tipo"] == "percent":
        v = _num(mx.group(1))
        if v and (not out.get("minimo") or v != out["minimo"]):
            out["maximo"] = v
    return out


# ------------------------------------------------------------------
# Aviso de cupom esgotado nos canais
# -----------------------------------------------------------------
_ESG_RE = re.compile(r"\besgot|\bacabou\b|\bacabaram\b|\bencerr|\bexpirou\b|\bparou de funcionar|\bnao (?:esta|ta) mais funcionando|\bfoi desativad")
_NAO_CODIGO = {
    "CUPOM", "CUPONS", "ESGOTADO", "ESGOTADOS", "ESGOTOU", "ESGOTADA", "ACABOU", "OFF", "PIX", "ML", "SHOPEE", "AMAZON",
    "MAGALU", "MERCADO", "LIVRE", "ATENCAO", "AVISO", "URGENTE", "HOJE", "AGORA", "BLACK", "FRIDAY", "LINK", "FRETE",
    "GRATIS", "PRIME", "APP", "NOVO", "VOLTOU", "ENCERRADO", "EXPIROU", "ALERTA", "CODIGO", "SITE", "LOJA", "BRL", "RS",
}


def cupons_esgotados(texto):
    """Se o post é um aviso de 'cupom esgotado/acabou', devolve os códigos citados; senão []."""
    t = texto or ""
    if len(t) > 500 or PRECO_RE.search(t) and re.search(r"\bpor\b|pix|a vista", norm(t)):
        return []
    n = norm(t)
    if not _ESG_RE.search(n) or not re.search(r"cupo|codigo|\b[A-Z0-9]{4,}\b", t):
        return []
    cods = []
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,24}", sem_acento(t)):
        tok = tok.strip("-_")
        if not (tok.isupper() or any(c.isdigit() for c in tok)) or len(tok) < 4:
            continue
        if not re.search(r"[A-Z]", tok) or tok.isdigit():
            continue
        if tok.upper() in _NAO_CODIGO or re.match(r"^\d+(OFF|X|GB|TB|MM|CM|ML|KG|W)$", tok.upper()):
            continue
        if tok.upper() not in cods:
            cods.append(tok.upper())
    return cods[:5]


def tem_sacola_laranja(texto):
    """🛍 (sacola de compras) — marca de posts que você não quer copiar."""
    return "\U0001F6CD" in (texto or "")
