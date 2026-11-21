"""Vigia de cupons: fica ligado o dia todo escutando os canais de cupom PELA SUA CONTA do Telegram.

Quando um canal posta cupom, manda "/canais ja" ao @AchadosFutricaBot (o radar do Google lê os canais
na mesma hora e gera/pergunta como sempre) e apaga o comando da conversa logo depois.
Assim o cupom chega em segundos, em vez de esperar o radar de 5 em 5 min.

Madrugada (00:30 às 07:00): o vigia não fica ligado; o radar do Google continua de backup.
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

from . import texto as T

BRT = timezone(timedelta(hours=-3))
_CUPOM = re.compile(r"cupo[mn]|codigo|resgat")


def eh_cupom(txt):
    return bool(_CUPOM.search(T.norm(txt or "")))


def _min(hhmm):
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def noite(cfg, agora=None):
    vc = cfg.get("vigia") or {}
    t = datetime.fromtimestamp(agora if agora is not None else time.time(), BRT)
    hm = t.hour * 60 + t.minute
    a, b = _min(vc.get("noite_inicio", "00:30")), _min(vc.get("noite_fim", "07:00"))
    return (a <= hm < b) if a < b else (hm >= a or hm < b)


class Vigia:
    def __init__(self, client, cfg, log):
        vc = cfg.get("vigia") or {}
        self.c, self.log = client, log
        self.robo = cfg["robo_cupons"]
        self.canais = [str(x).lstrip("@").lower() for x in vc.get("canais", [])]
        self.comando = vc.get("comando", "/canais ja")
        self.espera = float(vc.get("espera_seg", 15))       # dá tempo do post aparecer na página pública
        self.apagar = float(vc.get("apagar_seg", 45))
        self.min_entre = float(vc.get("min_entre_seg", 20))
        self.poll = float(vc.get("poll_seg", 20))
        self.pausa = float(vc.get("pausa_canal_seg", 1))
        self.vistos = {}          # canal -> maior id já visto
        self.agendado = False
        self.ultimo_envio = 0.0
        self.disparos = 0
        self._tarefas = []
        self._handler = None

    # ---------- decide ----------
    def novo(self, canal, mid, txt):
        """True se é post novo com cupom (e marca como visto). Evita disparar 2x (evento + leitura)."""
        canal = (canal or "").lower()
        if canal not in self.canais:
            return False
        ult = self.vistos.get(canal)
        if ult is not None and mid <= ult:
            return False
        self.vistos[canal] = max(mid, ult or 0)
        return ult is not None and eh_cupom(txt)   # 1ª leitura só marca o ponto de partida

    async def disparar(self, origem):
        if self.agendado:
            return                 # já tem um disparo marcado: ele cobre este post também
        self.agendado = True
        m = None
        try:
            await asyncio.sleep(self.espera)
            falta = self.min_entre - (time.time() - self.ultimo_envio)
            if falta > 0:
                await asyncio.sleep(falta)
            m = await self.c.send_message(self.robo, self.comando)
            self.ultimo_envio = time.time()
            self.disparos += 1
            self.log("vigia: cupom em %s -> radar chamado (%s)" % (origem, self.comando))
        except Exception as e:  # noqa
            self.log("aviso: vigia: nao consegui chamar o radar: %r" % e)
        finally:
            self.agendado = False
        if m is not None:
            await asyncio.sleep(self.apagar)
            try:
                await self.c.delete_messages(self.robo, [m.id])
            except Exception as e:  # noqa
                self.log("aviso: vigia: nao apaguei o comando: %r" % e)

    # ---------- escuta ----------
    async def _evento(self, ev):
        try:
            ch = await ev.get_chat()
            nome = (getattr(ch, "username", "") or "").lower()
            if self.novo(nome, ev.message.id, ev.message.message):
                asyncio.ensure_future(self.disparar("@" + nome))
        except Exception as e:  # noqa
            self.log("aviso: vigia (evento): %r" % e)

    async def _ler(self, canal):
        msgs = await self.c.get_messages(canal, limit=5)
        for m in sorted([x for x in msgs if x], key=lambda x: x.id):
            if self.novo(canal, m.id, m.message or ""):
                asyncio.ensure_future(self.disparar("@" + canal))

    async def _rodada(self):
        """Leitura de segurança (vale para canal em que a conta não está inscrita: aí não chega evento)."""
        while True:
            for canal in self.canais:
                try:
                    await self._ler(canal)
                except Exception as e:  # noqa
                    espera = getattr(e, "seconds", None)
                    if espera:                    # FloodWait: o Telegram pediu calma
                        self.log("vigia: Telegram pediu %ss de pausa" % espera)
                        await asyncio.sleep(min(int(espera) + 1, 600))
                    else:
                        self.log("aviso: vigia: nao li %s: %r" % (canal, e))
                await asyncio.sleep(self.pausa)
            await asyncio.sleep(self.poll)

    def ligar(self):
        from telethon import events
        loop = self.c.loop
        for canal in self.canais:              # ponto de partida: não dispara pelo que já estava lá
            try:
                loop.run_until_complete(self._ler(canal))
            except Exception as e:  # noqa
                self.log("aviso: vigia: nao li %s: %r" % (canal, e))
        self._handler = self._evento
        self.c.add_event_handler(self._handler, events.NewMessage(chats=self.canais))
        self._tarefas.append(loop.create_task(self._rodada()))
        self.log("vigia ligado: %s" % ", ".join(self.canais))

    def desligar(self):
        for t in self._tarefas:
            t.cancel()
        try:
            if not self.c.loop.is_running():
                self.c.loop.run_until_complete(asyncio.sleep(0))
        except Exception:  # noqa
            pass
        if self._handler:
            try:
                self.c.remove_event_handler(self._handler)
            except Exception:  # noqa
                pass
        self.log("vigia desligado (%d disparo(s))" % self.disparos)


def rodar_vigia(client, cfg, log, ciclo, salvar, dormir):
    """Fica ligado até `duracao_min` ou até a madrugada. Roda o ciclo de promoções a cada `ciclo_min`."""
    vc = cfg.get("vigia") or {}
    v = Vigia(client, cfg, log)
    v.ligar()
    inicio = time.time()
    dur = float(vc.get("duracao_min", 330)) * 60
    passo = float(vc.get("ciclo_min", 10)) * 60
    prox, prox_salvar = 0.0, time.time() + 1800
    try:
        while time.time() - inicio < dur and not noite(cfg):
            if time.time() >= prox and dur - (time.time() - inicio) > 15 * 60:   # não começa ciclo no fim
                prox = time.time() + passo
                try:
                    ciclo()
                except Exception as e:  # noqa
                    log("aviso: ciclo de promocoes falhou: %r" % e)
            if time.time() >= prox_salvar:
                prox_salvar = time.time() + 1800
                salvar()
            dormir(5)
    finally:
        v.desligar()
