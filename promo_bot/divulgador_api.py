"""Cupons na página de cupons do Divulgador Inteligente (a mesma API que o app usa).

Precisa dos secrets DI_EMAIL e DI_SENHA (login do app). Sem eles, nada é feito (o robô segue normal).
Endpoints (iguais ao app): POST /api/auth/local · GET/POST /api/coupons?bid= · PUT /api/coupons/<id>?bid=
"""
import json
import os
import urllib.error
import urllib.request

API = "https://api.divulgadorinteligente.com"
LOJA_DI = {"amazon": "amazon", "mercadolivre": "mercadolivre", "shopee": "shopee", "magalu": "magalu"}


def _brl(v):
    s = ("%.2f" % v).replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


def descricao(det):
    partes = []
    if det.get("minimo"):
        partes.append("Em compras acima de R$ %s" % _brl(det["minimo"]))
    if det.get("maximo"):
        partes.append("desconto máximo de R$ %s" % _brl(det["maximo"]))
    return (" - ".join(partes) + ".") if partes else ""


def corpo_cupom(loja, codigo, det):
    """Corpo no formato do app (seller, code, discount, discount_type, discount_max, ...)."""
    tipo = det.get("tipo")
    valor = det.get("valor")
    c = {
        "seller": LOJA_DI.get(loja, loja), "code": codigo.upper()[:50], "link": "", "featured": False,
        "expiration": None, "title": "Cupom %s" % {"mercadolivre": "Mercado Livre", "magalu": "Magalu"}.get(loja, loja.capitalize()),
        "description": descricao(det) or "Desconto exclusivo: ative seu cupom e aproveite a oferta!",
    }
    if tipo in ("percent", "fixed") and valor:
        c["discount"] = ("%g" % valor)
        c["discount_type"] = tipo
        c["discount_max"] = det.get("maximo") if tipo == "percent" and det.get("maximo") else None
    else:
        c["discount"] = "Aproveite agora o cupom"
        c["discount_type"] = "text"
        c["discount_max"] = None
    return c


class Divulgador:
    def __init__(self, email=None, senha=None, bid=None, http=None):
        self.email = email if email is not None else os.environ.get("DI_EMAIL", "")
        self.senha = senha if senha is not None else os.environ.get("DI_SENHA", "")
        self.bid = str(bid or os.environ.get("DI_BID", "9366"))
        self.http = http or self._http
        self.jwt = None

    @property
    def ligado(self):
        return bool(self.email and self.senha)

    @staticmethod
    def _http(metodo, url, corpo=None, token=None):
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(url, data=dados, method=metodo.upper())
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "achados-promo-bot")
        if dados is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "null")
            except Exception:  # noqa
                return e.code, None

    def _login(self):
        cod, j = self.http("post", API + "/api/auth/local", {"identifier": self.email, "password": self.senha})
        if cod >= 300 or not j or not j.get("jwt"):
            raise RuntimeError("login no Divulgador recusado (HTTP %s)" % cod)
        self.jwt = j["jwt"]

    def _api(self, metodo, caminho, corpo=None):
        if not self.jwt:
            self._login()
        cod, j = self.http(metodo, API + caminho, corpo, self.jwt)
        if cod in (401, 403):
            self._login()
            cod, j = self.http(metodo, API + caminho, corpo, self.jwt)
        return cod, j

    def listar(self):
        cod, j = self._api("get", "/api/coupons?bid=" + self.bid)
        if cod >= 300:
            raise RuntimeError("não consegui ler os cupons do Divulgador (HTTP %s)" % cod)
        if isinstance(j, dict):
            j = j.get("data") or j.get("results") or []
        return j or []

    def salvar_cupom(self, loja, codigo, det):
        """Cria ou atualiza o cupom (mesmo código e loja). Devolve 'criado' | 'atualizado' | 'igual'."""
        novo = corpo_cupom(loja, codigo, det)
        atual = None
        for c in self.listar():
            if str(c.get("code", "")).upper() == novo["code"] and str(c.get("seller", c.get("store", ""))) == novo["seller"]:
                atual = c
                break
        if atual:
            mudou = any(str(atual.get(k) or "") != str(novo.get(k) or "") for k in ("discount", "discount_type", "discount_max", "description"))
            if not mudou:
                return "igual"
            cod, _ = self._api("put", "/api/coupons/%s?bid=%s" % (atual["id"], self.bid), novo)
            if cod >= 300:
                raise RuntimeError("Divulgador recusou atualizar o cupom (HTTP %s)" % cod)
            return "atualizado"
        cod, _ = self._api("post", "/api/coupons?bid=" + self.bid, novo)
        if cod >= 300:
            raise RuntimeError("Divulgador recusou criar o cupom (HTTP %s)" % cod)
        return "criado"
