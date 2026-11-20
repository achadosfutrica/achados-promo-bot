"""Máquina de estados que conversa com o @di48291bot.

Regras de segurança:
 - modo test: preenche tudo e CANCELA no fim (nunca toca em GERAR PROMO).
 - tela desconhecida / botão não achado -> cancela o rascunho e avisa (nunca chuta).
 - nunca toca em 'parcel' (Add parcelamento).
"""
import re

from . import texto as T


class Tela:
    def __init__(self, texto="", botoes=None):
        self.texto = texto or ""
        self.botoes = botoes or []  # lista de rótulos (str), em ordem

    def achar(self, *trechos):
        """Procura botão. Trechos curtos (<=4 letras, ex.: 'por') só casam como PALAVRA inteira."""
        for b in self.botoes:
            nb = T.norm(b)
            for t in trechos:
                nt = T.norm(t)
                if len(nt) <= 4:
                    if re.search(r"(?<![a-z0-9])" + re.escape(nt) + r"(?![a-z0-9])", nb):
                        return b
                elif nt in nb:
                    return b
        return None

    def carregando(self):
        """Tela intermediária ('processando...') sem botões: ainda não é a resposta final."""
        return (not self.botoes) and self.tem_texto("processando", "carregando", "aguarde", "buscando", "analisando", "gerando")

    def tem_texto(self, *trechos):
        n = T.norm(self.texto)
        return any(T.norm(t) in n for t in trechos)


class Falha(Exception):
    pass


class Driver:
    def __init__(self, conv, cfg, log=print):
        """conv: objeto com enviar(txt)->Tela, clicar(rotulo)->Tela, responder(txt)->Tela, cancelar_tudo()"""
        self.conv, self.cfg, self.log = conv, cfg, log

    # --- helpers
    def _clicar(self, tela, *trechos, obrigatorio=True):
        b = tela.achar(*trechos)
        if b is None:
            if obrigatorio:
                raise Falha("botao nao encontrado: %s | botoes=%s" % (trechos, tela.botoes))
            return tela
        if "parcel" in T.norm(b) and not any("parcel" in T.norm(t) for t in trechos):
            raise Falha("recusei clicar em parcelamento")
        return self.conv.clicar(b)

    def _revisar(self, tela):
        """Pula a tela de revisão automática (always_review) se aparecer."""
        for _ in range(3):
            if tela.achar("gerar promo"):
                return tela
            b = tela.achar("continuar", "revisar", "seguir", "ok, entendi", "confirmar produto")
            if not b:
                break
            tela = self.conv.clicar(b)
        return tela

    PLATAFORMAS = ("awin", "rakuten", "afilio", "ltk", "lomadee", "flipnet")

    def _plataforma(self, tela, loja):
        """Se o bot perguntar por qual plataforma gerar (Awin/Rakuten/...), escolhe a do config."""
        for _ in range(2):
            if tela.achar("gerar promo"):
                return tela
            opcoes = [b for b in tela.botoes if any(p in T.norm(b) for p in self.PLATAFORMAS)]
            if not opcoes:
                return tela
            prefs = (self.cfg.get("plataforma") or {})
            ordem = [prefs.get(loja or "", "")] + list(prefs.get("_padrao", ["awin", "rakuten", "afilio"]))
            esc = None
            for p in [x for x in ordem if x]:
                esc = next((b for b in opcoes if T.norm(p) in T.norm(b)), None)
                if esc:
                    break
            if not esc:
                raise Falha("plataforma preferida nao oferecida: %s | botoes=%s" % (ordem, tela.botoes))
            self.log("  plataforma: %s" % esc)
            tela = self.conv.clicar(esc)
        return tela

    # --- fluxo
    def gerar(self, link, preco_txt, cupom=None, extra=None, modo="live", loja=None):
        """Retorna ('gerado'|'cancelado_teste', detalhe). Levanta Falha em qualquer anomalia."""
        try:
            if hasattr(self.conv, "limpar"):
                self.conv.limpar()          # fecha pergunta que ficou aberta (sua ou de outra execução)
            tela = self.conv.enviar(link)
            tela = self._plataforma(tela, loja)
            tela = self._revisar(tela)
            if tela.tem_texto("ja foi gerado", "já foi gerado", "ja gerou", "duplicad", "produto ja gerado", "deseja gerar novamente"):
                self.conv.cancelar_tudo()
                return "duplicado", tela.texto[:120]
            if not tela.achar("gerar promo"):
                raise Falha("tela de revisao nao reconhecida: %r | botoes=%s" % (tela.texto[:200], tela.botoes))

            # 1) CUPOM PRIMEIRO (ele recalcula desconto sozinho)
            if cupom:
                tela = self._clicar(tela, "cupom")
                tela = self.conv.responder(cupom)
                if tela.tem_texto("nao encontr", "não encontr", "inexistente", "invalido", "inválido", "nao existe"):
                    raise Falha("cupom desconhecido do Divulgador: %s" % cupom)
                tela = self._revisar(tela)

            # 2) PREÇO POR ÚLTIMO
            tela = self._clicar(tela, "por", "preco", "preço", "valor")
            tela = self.conv.responder(preco_txt)
            tela = self._revisar(tela)

            # 3) informação extra (opcional)
            if extra:
                tela = self._clicar(tela, "mais opc", "mais opç")
                tela = self._clicar(tela, "informacao extra", "informação extra", "info extra")
                tela = self.conv.responder(extra)
                tela = self._revisar(tela)

            if not tela.achar("gerar promo"):
                raise Falha("perdi o botao GERAR PROMO")

            if modo != "live":
                self.conv.cancelar_tudo()
                return "cancelado_teste", "preencheu e cancelou"

            tela = self._clicar(tela, "gerar promo")
            return "gerado", tela.texto[:200]
        except Falha:
            try:
                self.conv.cancelar_tudo()
            except Exception as e:  # noqa
                self.log("aviso: falha ao cancelar: %s" % e)
            raise
