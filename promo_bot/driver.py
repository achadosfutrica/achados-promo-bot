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


_GENERICAS = set("""para com sem kit unid unidades und cor tamanho preto preta branco branca azul verde rosa cinza
vermelho vermelha novo nova original oficial promo oferta loja frete gratis produto masculino feminino infantil adulto
the and pack caixa ml litro litros""".split())


def _palavras_fortes(s):
    import re
    n = T.norm(s or "")
    return {w for w in re.findall(r"[a-z0-9]{4,}", n) if w not in _GENERICAS and not w.isdigit()}


def produto_confere(texto_tela, titulo_post):
    """True = a tela mostra o produto do post · False = é OUTRO produto · None = não dá para saber.
    Compara as palavras fortes do título do post com o texto da tela de revisão."""
    pp = _palavras_fortes(titulo_post)
    if len(pp) < 2:
        return None
    tt = _palavras_fortes(texto_tela)
    if len(tt) < 3:
        return None
    comuns = pp & tt
    # nenhuma palavra em comum (nem prefixo de 5 letras) = produto trocado
    if comuns:
        return True
    pre_t = {w[:5] for w in tt if len(w) >= 5}
    if any(w[:5] in pre_t for w in pp if len(w) >= 5):
        return True
    return False


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
                raise Falha("botao nao encontrado: %s | tela=%r | botoes=%s" % (trechos, tela.texto[:250], tela.botoes))
            return tela
        if "parcel" in T.norm(b) and not any("parcel" in T.norm(t) for t in trechos):
            raise Falha("recusei clicar em parcelamento")
        return self.conv.clicar(b)

    def _revisar(self, tela):
        """Pula a tela de revisão automática (always_review) se aparecer."""
        for _ in range(4):
            if tela.achar("gerar promo"):
                return tela
            if tela.tem_texto("aplicar o desconto do cupom"):
                # o preço do post já vem com o cupom: NÃO deixa o Divulgador descontar de novo (o preço é digitado depois)
                sim = bool(self.cfg.get("aplicar_desconto_cupom", False))
                b = tela.achar("sim") if sim else tela.achar("nao", "não")
                if b:
                    self.log("  desconto do cupom: %s" % ("Sim" if sim else "Não"))
                    tela = self.conv.clicar(b)
                    continue
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

    def _manual(self, tela, dados):
        """Cadastro manual (loja que o Divulgador não lê, ex.: Centauro): responde as perguntas do bot
        com o que veio no post original (foto, título, preço, cupom). Pergunta desconhecida -> Falha (cancela)."""
        dados = dados or {}
        for _ in range(10):
            if tela.achar("gerar promo"):
                return tela
            n = T.norm(tela.texto)
            pular = tela.achar("pular", "nao tem", "não tem", "sem cupom", "nenhum", "skip")
            if "foto" in n or "imagem" in n:
                midia = dados.get("foto")() if callable(dados.get("foto")) else dados.get("foto")
                if not midia or not hasattr(self.conv, "enviar_foto"):
                    raise Falha("bot pediu foto e o post nao tem foto: %r" % tela.texto[:120])
                self.log("  manual: foto")
                tela = self.conv.enviar_foto(midia)
            elif "loja" in n and dados.get("loja_nome"):
                tela = self.conv.responder(dados["loja_nome"])
            elif "titulo" in n or "nome do produto" in n or "nome" in n:
                if not dados.get("titulo"):
                    raise Falha("bot pediu o titulo e nao tenho: %r" % tela.texto[:120])
                self.log("  manual: titulo")
                tela = self.conv.responder(dados["titulo"][:200])
            elif ("antigo" in n or "original" in n or "de quanto" in n or "preco de" in n or "cheio" in n):
                if dados.get("preco_antigo"):
                    tela = self.conv.responder(dados["preco_antigo"])
                elif pular:
                    tela = self.conv.clicar(pular)
                else:
                    tela = self.conv.responder(dados.get("preco_txt", ""))
            elif "preco" in n or "valor" in n:
                self.log("  manual: preco")
                tela = self.conv.responder(dados.get("preco_txt", ""))
            elif "cupom" in n:
                dados["_cupom_ok"] = True
                if dados.get("cupom"):
                    tela = self.conv.responder(dados["cupom"])
                elif pular:
                    tela = self.conv.clicar(pular)
                else:
                    raise Falha("bot pediu cupom e nao ha botao de pular: %r" % tela.texto[:120])
            elif "link" in n and dados.get("link"):
                tela = self.conv.responder(dados["link"])
            elif pular:
                tela = self.conv.clicar(pular)
            else:
                raise Falha("pergunta do cadastro manual nao reconhecida: %r | botoes=%s" % (tela.texto[:200], tela.botoes))
        raise Falha("cadastro manual nao terminou")

    @staticmethod
    def _ler_loja(tela):
        """(preco_atual, preco_antigo) que o Divulgador leu NA LOJA; 0 se nao leu."""
        atual = antigo = 0.0
        try:
            linhas = [x for x in ((tela.texto or "").split("\n") + list(tela.botoes or [])) if x]
            for ln in linhas:
                n = T.norm(ln).strip(" *_`:-")
                m = T.PRECO_RE.search(ln)
                if not m:
                    continue
                v = T._para_float(m.group(1), m.group(2))
                if re.match(r"(por|preco|valor|💸)", n) and not re.search(r"\d+\s*x|parcel|pix|vista|cupom", n) and not atual:
                    atual = v
                elif re.match(r"(de|antes|preco antigo|preco de)\b", n) and not antigo:
                    antigo = v
        except Exception:  # noqa
            pass
        return atual, antigo

    @staticmethod
    def _trava_valor(base, preco_txt):
        """base = (atual, antigo) lido ANTES de aplicar cupom. Cancela se o preco do post nao bater com a loja."""
        try:
            ours = T._para_float(*re.match(r"\s*([0-9.]+)(?:,([0-9]{1,2}))?", preco_txt.replace(".", "")).groups())
        except Exception:  # noqa
            return
        atual, antigo = base
        if atual and ours:
            if ours < atual * 0.85 or ours > atual * 1.05:
                raise Falha("VALOR NAO BATE: post R$ %s x loja R$ %s (lido antes do cupom) - nao gerei" % (preco_txt, ("%.2f" % atual).replace(".", ",")))
        elif antigo and ours and ours < antigo * 0.3:
            raise Falha("VALOR NAO CONFERIDO: o Divulgador nao leu o preco atual da loja; post R$ %s vs De R$ %s - nao gerei" % (preco_txt, ("%.2f" % antigo).replace(".", ",")))

    # --- fluxo
    def gerar(self, link, preco_txt, cupom=None, extra=None, modo="live", loja=None, dados=None):
        """Retorna ('gerado'|'cancelado_teste', detalhe). Levanta Falha em qualquer anomalia."""
        try:
            if hasattr(self.conv, "limpar"):
                self.conv.limpar()          # fecha pergunta que ficou aberta (sua ou de outra execução)
            if hasattr(self.conv, "zerar"):
                self.conv.zerar()           # e manda /cancelar: nenhuma pergunta antiga recebe os dados deste produto
            tela = self.conv.enviar(link)
            tela = self._plataforma(tela, loja)
            if (not tela.achar("gerar promo") and tela.tem_texto("informe os dados", "nao e de produto", "não é de produto")
                    and not tela.tem_texto("qual a foto", "qual o titulo", "qual o título") and hasattr(self.conv, "esperar")):
                try:
                    tela = self.conv.esperar()       # o bot manda a 1ª pergunta do cadastro manual logo depois
                except Exception:  # noqa
                    pass
            if not tela.achar("gerar promo") and tela.tem_texto("qual a foto", "foto do produto", "qual o titulo", "qual o título", "nome do produto"):
                self.log("  Divulgador nao leu a loja: cadastro manual com os dados do post")
                d = dict(dados or {})
                d.setdefault("preco_txt", preco_txt)
                d.setdefault("cupom", cupom)
                d.setdefault("link", link)
                tela = self._manual(tela, d)
                if d.get("_cupom_ok"):
                    cupom = None                 # já respondido no cadastro manual
            tela = self._revisar(tela)
            if tela.tem_texto("ja foi gerado", "já foi gerado", "ja gerou", "duplicad", "produto ja gerado", "deseja gerar novamente"):
                self.conv.cancelar_tudo()
                return "duplicado", tela.texto[:120]
            if not tela.achar("gerar promo"):
                raise Falha("tela de revisao nao reconhecida: %r | botoes=%s" % (tela.texto[:200], tela.botoes))
            confere = produto_confere(tela.texto, (dados or {}).get("titulo"))
            if confere is False:
                raise Falha("o produto da tela do Divulgador nao e o do post (%r x %r)" % (tela.texto[:120], (dados or {}).get("titulo", "")[:80]))

            base_loja = self._ler_loja(tela)      # preco REAL da loja, lido ANTES do cupom
            self.log("  loja (antes do cupom): atual=%s antigo=%s | post=%s" % (base_loja[0], base_loja[1], preco_txt))

            # 1) CUPOM PRIMEIRO (ele recalcula desconto sozinho)
            if cupom and any(T.norm(cupom) in T.norm(b) for b in tela.botoes):
                cupom = None                     # o Divulgador já colocou este cupom (ex.: botão "🎟️ TENISCERTO")
            if cupom:
                tela = self._clicar(tela, "cupom", "🎟")
                tela = self.conv.responder(cupom)
                if tela.tem_texto("nao encontr", "não encontr", "inexistente", "invalido", "inválido", "nao existe"):
                    raise Falha("cupom desconhecido do Divulgador: %s" % cupom)
                tela = self._revisar(tela)

            # 1b) preço antigo ("De:") quando o post traz e o Divulgador deixou vazio
            b_de = next((b for b in tela.botoes if T.norm(b).strip().startswith("de:") and "vazio" in T.norm(b)), None)
            if b_de and (dados or {}).get("preco_antigo"):
                tela = self.conv.clicar(b_de)
                tela = self.conv.responder(dados["preco_antigo"])
                tela = self._revisar(tela)

            # 2a) TRAVA DE VALOR: so cancela se o preco do post NAO bater com o preco REAL da loja (lido pelo Divulgador).
            #     Se bater (mesmo sendo bug/preco baixo), segue normal.
            self._trava_valor(base_loja, preco_txt)

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
