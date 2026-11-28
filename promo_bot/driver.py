"""Máquina de estados que conversa com o @di48291bot.

Regras de segurança:
 - modo test: preenche tudo e CANCELA no fim (nunca toca em GERAR PROMO).
 - tela desconhecida / botão não achado -> cancela o rascunho e avisa (nunca chuta).
 - nunca toca em 'parcel' (Add parcelamento).
"""
import re
import time

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
        """Tela intermediária ('processando...', 'aplicando...') sem botões: ainda não é a resposta final.
        v60: qualquer texto sem botão que termine em reticencias também conta (o Divulgador tem várias telas de
        status - 'Aplicando cupom...', 'Validando...', 'Confirmando...' - e tratar uma delas como resposta final
        cancelava o produto à toa; ver ATUALIZACAO de 25/11 ~09h10)."""
        if self.botoes:
            return False
        if self.tem_texto("processando", "carregando", "aguarde", "buscando", "analisando", "gerando",
                           "aplicando", "validando", "confirmando", "verificando", "atualizando",
                           "preparando", "montando", "conferindo", "calculando", "iniciando"):
            return True
        fim = re.sub(r"[\s\U0001F000-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF\uFE0F]+$", "", self.texto or "")
        return fim.endswith("...") or fim.endswith("\u2026")

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


def eh_resposta_cancelar(tela):
    """Resposta do bot ao /cancelar ('Cancelado. Envie um link...' / 'Nada pra cancelar no momento')."""
    n = T.norm(tela.texto)
    return (not tela.botoes) and len(n) < 160 and ("cancelad" in n or "nada pra cancelar" in n or "nada para cancelar" in n)


def botao_confirmar_cancelamento(botoes):
    """Na tela 'Tem certeza que deseja cancelar?' escolhe o botao que CONFIRMA (Sim); nunca 'Nao'/'Voltar'."""
    cands = [b for b in botoes if not re.search(r"(?<![a-z0-9])(nao|voltar|continuar|manter)(?![a-z0-9])", T.norm(b))]
    for pred in (lambda n: re.search(r"(?<![a-z0-9])sim(?![a-z0-9])", n), lambda n: "confirm" in n, lambda n: "cancel" in n):
        for b in cands:
            if pred(T.norm(b)):
                return b
    return None


def eh_confirmacao_cancelar(tela):
    """O Divulgador, ao receber /cancelar (ou clique em Cancelar) com uma tela de produto aberta, NAO cancela:
    edita a tela para 'Tem certeza que deseja cancelar?' e espera a confirmacao."""
    return "certeza" in T.norm(tela.texto) and botao_confirmar_cancelamento(tela.botoes) is not None


# 28/11: pergunta "Qual perfil Awin deseja utilizar?" (botoes "Dafiti BR" / "Dafiti Fashion Creators BR"). Regra do Oscar:
# Dafiti = SEMPRE "Dafiti BR". Antes o robo nao reconhecia os botoes, deixava a pergunta aberta e o bot do Divulgador
# ficava preso (nao respondia mais ao /cancelar) - 2 dos 5 bots ficaram assim por horas.
PERFIS_AWIN = ("dafiti br",)


def eh_pergunta_perfil(tela):
    return bool(tela.botoes) and "qual perfil" in T.norm(tela.texto)


def escolher_perfil(botoes, prefs=None, estrito=True):
    """Escolhe o botao de perfil: o preferido (padrao 'Dafiti BR'), nunca um 'Creators'.
    estrito=False (so para destravar um pedido que vai ser CANCELADO): na falta do preferido, o primeiro que nao e 'Creators'."""
    ps = [T.norm(p) for p in (prefs or PERFIS_AWIN) if p]
    cands = [b for b in botoes if "creator" not in T.norm(b)]
    for p in ps:
        for b in cands:
            if p in T.norm(b):
                return b
    if not estrito and cands:
        return cands[0]
    return None


class Falha(Exception):
    pass


# 28/11: TRAVA DE VALOR v2. O preco do post costuma JA vir com um desconto que a vitrine da loja (lida pelo Divulgador)
# nao mostra: codigo de cupom sozinho numa linha (BLACKMELI), "RESGATE O CUPOM...", "desconto na finalizacao" (Amazon).
# A regra de sempre (post ate 15% abaixo da loja) barrava esses posts. A v2 so roda quando a regra de sempre BARRARIA
# e compara o preco do post com o que o cupom explica. Nunca afrouxa o teto (post mais caro que a loja).
_NAO_COD = set(T._NAO_CODIGO) | set(T._LOJAS_NOME) | {
    "COMPRE", "AQUI", "ABAIXO", "PRECO", "BUG", "PROMO", "OFERTA", "DESCONTO", "RESGATE", "RESGATAR", "ATIVAR", "ATIVE",
    "FRETE", "GRATIS", "FULL", "OFICIAL", "OFICIAIS", "LOJAS", "PAGINA", "CHECKOUT", "FINALIZACAO", "FINALIZAR",
}
_LINHA_COD = re.compile(r"^[^\w]*([A-Z0-9][A-Z0-9_\-]{3,23})[^\w]*$")
_SINAL_DESC = re.compile(
    r"resgat\w*\s+(?:o\s+)?cupom|aplique\s+o\s+cupom|cupom\s+(?:na|no|do|da)\s+(?:pagina|app|site|anuncio)|"
    r"desconto\s+(?:na|ao|no)\s+(?:finaliza\w*|checkout|carrinho|pagamento)|(?:ao|na)\s+finalizar|no\s+checkout|"
    r"cai\s+no\s+carrinho|desconto\s+(?:extra\s+)?(?:na|ao)\s+finalizacao")
_PISO_SINAL_SEM_VALOR, _TETO_V2, _FOLGA_CUPOM = 0.70, 1.05, 0.94


def codigo_solto(texto):
    """Codigo de cupom escrito sozinho numa linha (BLACKMELI, CUPONOMOVEIS...). Nunca a 1a linha (titulo)."""
    linhas = [l for l in (texto or "").splitlines() if l.strip()]
    for i, l in enumerate(linhas):
        if i == 0:
            continue
        s = T.sem_acento(l).strip()
        m = _LINHA_COD.match(s)
        if not m or s != s.upper():
            continue
        c = m.group(1)
        if c.isdigit() or not re.search(r"[A-Z]", c) or c in _NAO_COD or re.match(r"^\d+(OFF|X|GB|TB|MM|CM|ML|KG|W)$", c):
            continue
        return c
    return None


def sinal_desconto(texto):
    """(tem_sinal, motivo, codigo): o preco do post ja leva um desconto que a vitrine da loja nao mostra."""
    cod = T.cupom(texto)
    if cod and cod in _NAO_COD:
        cod = None
    cod = cod or codigo_solto(texto)
    if cod:
        return True, "codigo %s" % cod, cod
    m = _SINAL_DESC.search(T.norm(texto or ""))
    if m:
        return True, "texto '%s'" % m.group(0), None
    return False, "", None


def linhas_extra(texto, cod):
    """Linhas que o post precisa levar na informacao extra quando a v2 libera: o codigo e/ou a frase do desconto."""
    out = []
    if cod:
        out.append("🎟️ Cupom: %s" % cod)
    for l in (texto or "").splitlines():
        s = l.strip()
        if s and len(s) <= 120 and not T.URL_RE.search(s) and _SINAL_DESC.search(T.norm(s)) and s not in out:
            out.append(s)
        if len(out) >= 3:
            break
    return out


def detalhes_do_cupom(texto, cod, cache):
    """Valor do desconto: o que o post diz; se nao disser, o que o robo ja aprendeu desse codigo (est.d['cupom_min'])."""
    det = T.cupom_detalhes(texto) or {}
    if not det.get("valor") and cod and isinstance((cache or {}).get(cod), dict) and cache[cod].get("valor"):
        c = cache[cod]
        det = {"tipo": c.get("tipo"), "valor": c.get("valor")}
    return det if det.get("valor") else {}


def preco_esperado(ref, det):
    """Preco que a loja deveria mostrar depois do cupom. A compra minima NAO anula o cupom aqui: nos casos reais
    (BLACKMELI min R$ 199) o post bateu exato com o desconto mesmo com o produto abaixo do minimo."""
    if det.get("tipo") == "percent":
        d = ref * det["valor"] / 100.0
        if det.get("maximo"):
            d = min(d, det["maximo"])
    else:
        d = det["valor"]
    return ref - d


def trava_v2(base, preco_txt, texto, cache=None):
    """(ok, nivel, motivo, codigo). nivel A = bate com o cupom; B = ha sinal de desconto mas sem valor conhecido."""
    atual, _antigo = base
    ours = T._para_float(*re.match(r"\s*([0-9.]+)(?:,([0-9]{1,2}))?", preco_txt.replace(".", "")).groups())
    if not (atual and ours):
        return False, "-", "sem preco para comparar", None
    tem, why, cod = sinal_desconto(texto)
    if not tem:
        return False, "N", "o texto do post nao cita cupom nem desconto", cod
    r = ours / atual
    if r > _TETO_V2:
        return False, "teto", "post %.0f%% da loja (acima de 105%%)" % (r * 100), cod
    det = detalhes_do_cupom(texto, cod, cache)
    if det:
        exp = preco_esperado(atual, det)
        if ours >= exp * _FOLGA_CUPOM:
            return True, "A", "bate com o cupom (%s): esperado R$ %.2f, post R$ %.2f" % (why, exp, ours), cod
        return False, "A", "post R$ %.2f esta %.0f%% abaixo do que o cupom explica (R$ %.2f)" % (ours, (1 - ours / exp) * 100, exp), cod
    if r >= _PISO_SINAL_SEM_VALOR:
        return True, "B", "sinal de desconto (%s) sem valor conhecido; post %.0f%% da loja" % (why, r * 100), cod
    return False, "B", "sinal (%s) sem valor conhecido, mas post %.0f%% da loja (piso 70%%)" % (why, r * 100), cod


class Driver:
    def __init__(self, conv, cfg, log=print):
        """conv: objeto com enviar(txt)->Tela, clicar(rotulo)->Tela, responder(txt)->Tela, cancelar_tudo()"""
        self.conv, self.cfg, self.log = conv, cfg, log
        if hasattr(conv, "timeout_link"):
            conv.log = log          # a conversa avisa quando ignora tela atrasada do Divulgador

    @staticmethod
    def _aceitar_link(dados):
        """Filtro da 1ª resposta ao link: ignora resposta atrasada de OUTRO pedido (Divulgador lento)
        - sobra do /cancelar ou tela de outro produto - e espera a do nosso link."""
        titulo = (dados or {}).get("titulo")

        def ok(tela):
            if eh_resposta_cancelar(tela) or eh_confirmacao_cancelar(tela):
                return False
            return produto_confere(tela.texto, titulo) is not False
        return ok

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
        for _ in range(3):
            if tela.achar("gerar promo"):
                return tela
            if eh_pergunta_perfil(tela):
                esc = escolher_perfil(tela.botoes, self.cfg.get("perfil_awin"))
                if not esc:
                    raise Falha("perfil Awin nao reconhecido (loja=%s) | botoes=%s" % (loja or "?", tela.botoes))
                self.log("  perfil Awin: %s" % esc)
                tela = self.conv.clicar(esc)
                continue
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

    def _trava(self, base, preco_txt, dados, extra, cupom_no_post=None):
        """Trava de valor: regra de sempre + v2 (28/11). Devolve a informacao extra (pode ganhar o cupom/desconto do post).
        Se a regra de sempre aprova, NADA muda. Se ela barra com "VALOR NAO BATE", a v2 tenta explicar a diferenca pelo
        cupom/desconto que o post cita; qualquer erro da v2 cai de volta na regra de sempre.
        Desliga com `trava_valor_v2: false` no config.yaml."""
        try:
            self._trava_valor(base, preco_txt)
            return extra
        except Falha as e_old:
            texto_post = (dados or {}).get("texto")
            if "VALOR NAO BATE" not in str(e_old) or not texto_post or not self.cfg.get("trava_valor_v2", True):
                raise
            try:
                ok, nivel, motivo, cod = trava_v2(base, preco_txt, texto_post, (dados or {}).get("cupom_cache"))
                linhas = linhas_extra(texto_post, cod) if ok else []
            except Exception as e_v2:  # noqa
                self.log("  aviso: trava v2 com erro (%r): vale a regra de sempre" % (e_v2,))
                raise e_old
            if not ok:
                raise Falha("%s [v2: %s]" % (e_old, motivo))
            self.log("  trava de valor v2 LIBEROU (nivel %s): %s" % (nivel, motivo))
            if cod and cupom_no_post and cod == cupom_no_post:
                linhas = [l for l in linhas if not l.startswith("🎟️ Cupom:")]   # o Divulgador ja poe este cupom na mensagem
            ja = T.norm(extra or "")
            novas = [l for l in linhas if T.norm(l) not in ja]
            if novas:
                extra = "\n".join(novas) + (("\n\n" + extra) if extra else "")
                self.log("  extra com o cupom/desconto do post: " + " / ".join(novas)[:140])
            return extra

    # --- fluxo
    def gerar(self, link, preco_txt, cupom=None, extra=None, modo="live", loja=None, dados=None):
        """Retorna ('gerado'|'cancelado_teste', detalhe). Levanta Falha em qualquer anomalia."""
        cupom_no_post = cupom      # cupom que o robo leu no texto do post (o Divulgador o poe na mensagem)
        try:
            if hasattr(self.conv, "limpar"):
                self.conv.limpar()          # fecha pergunta que ficou aberta (sua ou de outra execução)
            if hasattr(self.conv, "zerar"):
                self.conv.zerar()           # e manda /cancelar: nenhuma pergunta antiga recebe os dados deste produto
            for _tent in (1, 2, 3):
                try:
                    tela = self.conv.enviar(link, timeout=getattr(self.conv, "timeout_link", None), aceitar=self._aceitar_link(dados))
                    tela = self._plataforma(tela, loja)
                    if not tela.botoes and tela.tem_texto("erro inesperado"):
                        # instabilidade conhecida da rede de afiliados (ex.: Awin) ao buscar o produto;
                        # nao e erro nosso e repetir na hora so vai dar o mesmo resultado - ver 27/11
                        raise Falha("AWIN INSTAVEL: o Divulgador respondeu 'erro inesperado' ao buscar o produto (loja=%s)" % (loja or "?"))
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
                    break
                except (Falha, TimeoutError, RuntimeError) as e_t:
                    m_t = str(e_t)
                    if _tent == 3 or not re.search(r"nao e o do post|revisao nao reconhecida|botao sumiu|cadastro manual nao terminou|perdi o botao", m_t):
                        raise
                    self.log("  tentativa %d/3 falhou (%s): reinicio a conversa com o bot e tento de novo" % (_tent, m_t[:110]))
                    for fn in ("cancelar_tudo", "zerar"):
                        try:
                            getattr(self.conv, fn)()
                        except Exception:  # noqa
                            pass
                    time.sleep(8 * _tent)

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
            extra = self._trava(base_loja, preco_txt, dados, extra, cupom_no_post)    # v2 28/11: ver _trava

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
