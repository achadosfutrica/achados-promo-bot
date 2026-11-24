"""Conversa real via Telethon (conta de usuário). Só é importado quando mode != off/dry."""
import asyncio
import time

from . import texto as T
from .driver import Tela, botao_confirmar_cancelamento, eh_confirmacao_cancelar, eh_resposta_cancelar


def _tela(msg):
    botoes = []
    if msg is not None and getattr(msg, "buttons", None):
        for linha in msg.buttons:
            for b in linha:
                botoes.append(b.text)
    return Tela(getattr(msg, "message", "") or "", botoes)


class ConvTelethon:
    """Versão síncrona sobre o loop do Telethon (simples de testar e de ler)."""

    def __init__(self, client, bot_user, timeout=90, intervalo=1.5, timeout_link=150):
        self.c, self.bot, self.timeout, self.intervalo = client, bot_user, timeout, intervalo
        self.timeout_link = timeout_link
        self.loop = client.loop
        self._ultima = None
        self.log = lambda s: None            # o Driver troca por log de verdade

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _assinatura(self):
        """Última mensagem RECEBIDA do bot (ignora as nossas, senão agiríamos antes da resposta)."""
        msgs = await self.c.get_messages(self.bot, limit=8)
        m = next((x for x in msgs if not x.out), None)
        if m is None:
            return None, ()
        return m, (m.id, m.message, tuple(b.text for l in (m.buttons or []) for b in l), m.edit_date)

    async def _esperar_mudanca(self, antes, min_id=None, timeout=None, aceitar=None):
        """Espera a resposta do bot. min_id: só vale mensagem do bot DEPOIS da nossa (evita ler uma tela velha).
        aceitar(tela)->bool: o Divulgador responde UM pedido por vez e pode atrasar minutos; uma resposta
        atrasada de OUTRO pedido chega depois da nossa mensagem e parecia ser a nossa. Se aceitar() recusa,
        ignora essa tela e continua esperando a certa."""
        fim = time.time() + (timeout or self.timeout)
        estavel, ult = 0, None
        ignorada = None
        while time.time() < fim:
            await asyncio.sleep(self.intervalo)
            m, ass = await self._assinatura()
            if m is None or (min_id is not None and m.id <= min_id):
                continue
            if ass != antes:
                # espera estabilizar (o bot edita a mensagem em etapas)
                if ass == ult:
                    estavel += 1
                    if estavel >= 1:
                        t = _tela(m)
                        if t.carregando():      # "processando..." -> continua esperando
                            continue
                        if aceitar is not None and not aceitar(t):
                            ignorada = (t.texto or "")[:90].replace("\n", " ")
                            try:
                                self.log("  ignorei tela de outro pedido (Divulgador atrasado): %r" % ignorada)
                            except Exception:  # noqa
                                pass
                            antes, ult, estavel = ass, None, 0
                            continue
                        self._ultima = m
                        return t
                else:
                    ult, estavel = ass, 0
        if ignorada:
            raise TimeoutError("o bot nao respondeu a tempo com o produto certo em %ss (so chegaram telas de outro pedido, ultima: %r)" % (timeout or self.timeout, ignorada))
        raise TimeoutError("o bot nao respondeu em %ss" % (timeout or self.timeout))

    def enviar(self, txt, timeout=None, aceitar=None):
        async def f():
            _, antes = await self._assinatura()
            enviada = await self.c.send_message(self.bot, txt)
            return await self._esperar_mudanca(antes, min_id=getattr(enviada, "id", None), timeout=timeout, aceitar=aceitar)
        return self._run(f())

    def responder(self, txt):
        return self.enviar(txt)

    def esperar(self, timeout=20):
        """Espera a próxima mensagem do bot sem enviar nada (o bot manda 2 mensagens seguidas)."""
        async def f():
            _, antes = await self._assinatura()
            return await self._esperar_mudanca(antes, timeout=timeout)
        return self._run(f())

    def enviar_foto(self, midia):
        """Manda a foto do post original (cadastro manual: 'Qual a foto do produto?')."""
        async def f():
            _, antes = await self._assinatura()
            enviada = await self.c.send_file(self.bot, midia)
            return await self._esperar_mudanca(antes, min_id=getattr(enviada, "id", None))
        return self._run(f())

    def clicar(self, rotulo):
        async def f():
            m, antes = await self._assinatura()
            alvo = None
            for linha in ((m.buttons if m else None) or []):
                for b in linha:
                    if b.text == rotulo:
                        alvo = b
            if alvo is None:
                raise RuntimeError("botao sumiu: " + rotulo)
            await alvo.click()
            return await self._esperar_mudanca(antes)
        return self._run(f())

    async def _confirmar_cancelamento(self, timeout=15):
        """Se a ultima tela do bot e 'Tem certeza que deseja cancelar?', clica em Sim e espera a resposta.
        Retorna None (nao era confirmacao), a Tela que veio depois do clique, ou False (clicou, sem resposta a tempo)."""
        m, antes = await self._assinatura()
        if m is None or not m.buttons:
            return None
        t = _tela(m)
        if not eh_confirmacao_cancelar(t):
            return None
        rotulo = botao_confirmar_cancelamento(t.botoes)
        alvo = next((b for l in m.buttons for b in l if b.text == rotulo), None)
        if alvo is None:
            return None
        try:
            self.log("  Divulgador pediu confirmacao (%r | %s): confirmo o cancelamento" % ((t.texto or "")[:60].replace("\n", " "), t.botoes))
        except Exception:  # noqa
            pass
        await alvo.click()
        try:
            return await self._esperar_mudanca(antes, timeout=timeout)
        except TimeoutError:
            return False

    def cancelar_tudo(self):
        """Fecha qualquer tela aberta do bot (Cancelar -> Sim / Nao) e ESPERA a resposta, para não atropelar o próximo produto."""
        async def f():
            m, antes = await self._assinatura()
            if await self._confirmar_cancelamento() is not None:      # ja estava na pergunta 'Tem certeza...?': confirma
                return True
            if m and not m.buttons and "/cancelar" in (m.message or ""):
                # pergunta do cadastro manual ("Qual a foto do produto? /cancelar"): cancela por comando
                enviada = await self.c.send_message(self.bot, "/cancelar")
                try:
                    await self._esperar_mudanca(antes, min_id=getattr(enviada, "id", None), timeout=15)
                except TimeoutError:
                    pass
                await self._confirmar_cancelamento()
                return True
            if not (m and m.buttons):
                return False
            alvo = None
            for linha in m.buttons:
                for b in linha:
                    nb = T.norm(b.text or "")
                    if "cancel" in nb or nb.strip(" ❌✖️") in ("nao", "não") or nb.startswith(("❌ nao", "nao,")):
                        alvo = alvo or b
            if alvo is None:
                return False
            await alvo.click()
            try:
                await self._esperar_mudanca(antes, timeout=15)
            except TimeoutError:
                pass
            await self._confirmar_cancelamento()          # o Cancelar da tela de produto pede confirmacao
            return True
        return self._run(f())

    def zerar(self, espera=90):
        """Zera a conversa com o bot ANTES de cada produto: manda /cancelar e ESPERA a resposta DELE ao /cancelar.
        Como o Divulgador responde um pedido por vez, essa resposta so chega depois de ele terminar tudo que
        ainda devia (e atrasar minutos e comum): e uma BARREIRA - depois dela a fila esta vazia e o proximo link
        recebe a resposta dele, nao a de um link antigo. Com uma tela de produto aberta ele responde
        'Tem certeza que deseja cancelar?': confirma com Sim (sem isso a tela velha ficava presa e voltava no
        lugar de cada produto novo). Tambem fecha qualquer pergunta aberta
        (ex.: 'Qual o preço?' do produto que falhou recebia os dados do PRÓXIMO: o tênis Asics saiu por R$ 14)."""
        async def f():
            await self._confirmar_cancelamento()        # se ja ha um 'Tem certeza...?' aberto, confirma antes
            _, antes = await self._assinatura()
            enviada = await self.c.send_message(self.bot, "/cancelar")
            t0 = time.time()
            fim = t0 + espera
            # sem min_id: a confirmacao 'Tem certeza?' e uma EDICAO da tela de produto antiga (id menor que o
            # do nosso /cancelar) e seria ignorada; antes + aceita ja garantem que so conta mudanca nova.
            min_id = None
            ok = False                                     # True = o Divulgador respondeu ao /cancelar (fila limpa)
            aceita = lambda x: eh_resposta_cancelar(x) or eh_confirmacao_cancelar(x)
            for _ in range(4):
                restante = fim - time.time()
                if restante <= 0:
                    break
                try:
                    t = await self._esperar_mudanca(antes, min_id=min_id, timeout=restante, aceitar=aceita)
                except TimeoutError:
                    try:
                        self.log("  aviso: o Divulgador nao respondeu ao /cancelar em %ss (fila lenta)" % espera)
                    except Exception:  # noqa
                        pass
                    break
                if eh_resposta_cancelar(t):
                    if time.time() - t0 > 12:
                        try:
                            self.log("  Divulgador demorou %ds para responder ao /cancelar (fila lenta)" % (time.time() - t0))
                        except Exception:  # noqa
                            pass
                    ok = True
                    break
                res = await self._confirmar_cancelamento(timeout=max(5, min(30, fim - time.time())))
                if res is not None and res is not False and eh_resposta_cancelar(res):
                    ok = True
                    break                                  # confirmei e ele respondeu 'Cancelado': fila limpa
                min_id = None
                _, antes = await self._assinatura()
            return ok
        try:
            return self._run(f())
        except Exception:  # noqa
            return False

    def limpar(self):
        """Antes de cada produto: se ficou uma pergunta aberta (ex.: 'Deseja gerar novamente?'), fecha."""
        try:
            return self.cancelar_tudo()
        except Exception:  # noqa
            return False


def resolver_bots(client, bots, log=print):
    """Confere (uma vez) quais bots da lista existem de verdade; os que nao existem sao ignorados com aviso."""
    ok = []
    for b in bots:
        b = str(b).strip().lstrip("@")
        if not b or b in ok:
            continue
        try:
            client.get_input_entity(b)
            ok.append(b)
        except Exception as e:  # noqa
            log("aviso: bot @%s nao encontrado (%r): ignorado" % (b, e))
    return ok


class MultiConv:
    """Divide as promocoes entre VARIOS bots do Divulgador (mesma conta, mesmas configuracoes, mesmo destino).

    - cada promocao usa UM bot do comeco ao fim (limpar -> zerar -> link -> revisar -> gerar): o rodizio e
      feito por produto, entao cada bot descansa enquanto os outros trabalham e a fila dele nao acumula;
    - se o bot da vez nao responde ao /cancelar em espera_rapida s (fila lenta), troca NA HORA para o proximo
      e deixa o lento de fora por cooldown_seg (ele usa esse tempo para esvaziar a fila);
    - se todos estao lentos, espera o ultimo ate o fim (comportamento antigo)."""

    def __init__(self, convs, cooldown_seg=300, espera_rapida=30, agora=time.time):
        if not convs:
            raise ValueError("MultiConv sem bots")
        self.convs = list(convs)
        self.atual = self.convs[0]
        self._prox = 0
        self.lento_ate = {}
        self.cooldown_seg, self.espera_rapida, self._agora = cooldown_seg, espera_rapida, agora
        self._log = lambda s: None

    @property
    def log(self):
        return self._log

    @log.setter
    def log(self, f):
        self._log = f
        for c in self.convs:
            c.log = f

    @property
    def timeout_link(self):
        return self.atual.timeout_link

    @property
    def bot(self):
        return self.atual.bot

    def __getattr__(self, nome):
        # enviar / responder / clicar / esperar / enviar_foto / cancelar_tudo: vao para o bot desta promocao
        if nome in ("atual", "convs") or nome.startswith("__"):
            raise AttributeError(nome)
        return getattr(self.atual, nome)

    def _ordem(self):
        """Bots na ordem do rodizio: primeiro os que nao estao de castigo, depois os lentos (o que volta antes primeiro)."""
        n, agora = len(self.convs), self._agora()
        rot = [self.convs[(self._prox + k) % n] for k in range(n)]
        livres = [c for c in rot if self.lento_ate.get(id(c), 0) <= agora]
        lentos = sorted((c for c in rot if c not in livres), key=lambda c: self.lento_ate[id(c)])
        return livres + lentos

    def limpar(self):
        """Comeco de cada promocao: escolhe o bot da vez (rodizio) e fecha o que ficou aberto nele."""
        c = self._ordem()[0]
        self.atual = c
        self._prox = (self.convs.index(c) + 1) % len(self.convs)
        try:
            self._log("  bot desta promocao: @%s" % c.bot)
        except Exception:  # noqa
            pass
        return c.limpar()

    def zerar(self, espera=90):
        outros = [c for c in self._ordem() if c is not self.atual]
        ordem = [self.atual] + outros
        for k, c in enumerate(ordem):
            ultimo = k == len(ordem) - 1
            if c is not self.atual:
                self.atual = c
                self._prox = (self.convs.index(c) + 1) % len(self.convs)      # o rodizio segue a partir do bot que assumiu
                try:
                    self._log("  trocando para o bot @%s (o anterior esta lento)" % c.bot)
                except Exception:  # noqa
                    pass
                c.limpar()
            esp = espera if ultimo else min(espera, self.espera_rapida)
            if c.zerar(espera=esp):
                self.lento_ate.pop(id(c), None)
                return True
            self.lento_ate[id(c)] = self._agora() + self.cooldown_seg
            try:
                self._log("  @%s nao respondeu ao /cancelar em %ss: fica de fora por %d min" % (c.bot, esp, self.cooldown_seg // 60))
            except Exception:  # noqa
                pass
        return False
