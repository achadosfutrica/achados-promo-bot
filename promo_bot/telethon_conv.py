"""Conversa real via Telethon (conta de usuário). Só é importado quando mode != off/dry."""
import asyncio
import time

from . import texto as T
from .driver import Tela


def _tela(msg):
    botoes = []
    if msg is not None and getattr(msg, "buttons", None):
        for linha in msg.buttons:
            for b in linha:
                botoes.append(b.text)
    return Tela(getattr(msg, "message", "") or "", botoes)


class ConvTelethon:
    """Versão síncrona sobre o loop do Telethon (simples de testar e de ler)."""

    def __init__(self, client, bot_user, timeout=45, intervalo=1.5):
        self.c, self.bot, self.timeout, self.intervalo = client, bot_user, timeout, intervalo
        self.loop = client.loop
        self._ultima = None

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _assinatura(self):
        """Última mensagem RECEBIDA do bot (ignora as nossas, senão agiríamos antes da resposta)."""
        msgs = await self.c.get_messages(self.bot, limit=8)
        m = next((x for x in msgs if not x.out), None)
        if m is None:
            return None, ()
        return m, (m.id, m.message, tuple(b.text for l in (m.buttons or []) for b in l), m.edit_date)

    async def _esperar_mudanca(self, antes, min_id=None, timeout=None):
        """Espera a resposta do bot. min_id: só vale mensagem do bot DEPOIS da nossa (evita ler uma tela velha)."""
        fim = time.time() + (timeout or self.timeout)
        estavel, ult = 0, None
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
                        self._ultima = m
                        return t
                else:
                    ult, estavel = ass, 0
        raise TimeoutError("o bot nao respondeu em %ss" % (timeout or self.timeout))

    def enviar(self, txt):
        async def f():
            _, antes = await self._assinatura()
            enviada = await self.c.send_message(self.bot, txt)
            return await self._esperar_mudanca(antes, min_id=getattr(enviada, "id", None))
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

    def limpar(self):
        """Antes de cada produto: se ficou uma pergunta aberta (ex.: 'Deseja gerar novamente?'), fecha."""
        try:
            return self.cancelar_tudo()
        except Exception:  # noqa
            return False
