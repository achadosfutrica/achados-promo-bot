"""Conversa real via Telethon (conta de usuário). Só é importado quando mode != off/dry."""
import asyncio
import time

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

    async def _esperar_mudanca(self, antes):
        fim = time.time() + self.timeout
        estavel, ult = 0, None
        while time.time() < fim:
            await asyncio.sleep(self.intervalo)
            m, ass = await self._assinatura()
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
        raise TimeoutError("o bot nao respondeu em %ss" % self.timeout)

    def enviar(self, txt):
        async def f():
            _, antes = await self._assinatura()
            await self.c.send_message(self.bot, txt)
            return await self._esperar_mudanca(antes)
        return self._run(f())

    def responder(self, txt):
        return self.enviar(txt)

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
        async def f():
            m, _ = await self._assinatura()
            if m and m.buttons:
                for linha in m.buttons:
                    for b in linha:
                        if "cancel" in (b.text or "").lower():
                            await b.click()
                            return
        return self._run(f())
