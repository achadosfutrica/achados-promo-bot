"""Conversa real via Telethon (conta de usuário). Só é importado quando mode != off/dry."""
import asyncio
import time

from . import texto as T
from .driver import Tela, eh_resposta_cancelar


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

    def cancelar_tudo(self):
        """Fecha qualquer tela aberta do bot (Cancelar / Não) e ESPERA a resposta, para não atropelar o próximo produto."""
        async def f():
            m, antes = await self._assinatura()
            if m and not m.buttons and "/cancelar" in (m.message or ""):
                # pergunta do cadastro manual ("Qual a foto do produto? /cancelar"): cancela por comando
                enviada = await self.c.send_message(self.bot, "/cancelar")
                try:
                    await self._esperar_mudanca(antes, min_id=getattr(enviada, "id", None), timeout=15)
                except TimeoutError:
                    pass
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
            return True
        return self._run(f())

    def zerar(self, espera=90):
        """Zera a conversa com o bot ANTES de cada produto: manda /cancelar e ESPERA a resposta DELE ao /cancelar.
        Como o Divulgador responde um pedido por vez, essa resposta so chega depois de ele terminar tudo que
        ainda devia (e atrasar minutos e comum): e uma BARREIRA - depois dela a fila esta vazia e o proximo link
        recebe a resposta dele, nao a de um link antigo. Tambem fecha qualquer pergunta aberta
        (ex.: 'Qual o preço?' do produto que falhou recebia os dados do PRÓXIMO: o tênis Asics saiu por R$ 14)."""
        async def f():
            _, antes = await self._assinatura()
            enviada = await self.c.send_message(self.bot, "/cancelar")
            t0 = time.time()
            try:
                await self._esperar_mudanca(antes, min_id=getattr(enviada, "id", None), timeout=espera, aceitar=eh_resposta_cancelar)
                if time.time() - t0 > 12:
                    self.log("  Divulgador demorou %ds para responder ao /cancelar (fila lenta)" % (time.time() - t0))
            except TimeoutError:
                try:
                    self.log("  aviso: o Divulgador nao respondeu ao /cancelar em %ss (fila lenta)" % espera)
                except Exception:  # noqa
                    pass
            return True
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
