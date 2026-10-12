"""Cliente (conexão + controlador) contra o backend real, com a mídia simulada."""

from __future__ import annotations

import asyncio

import pytest

from app.services.transmissao import ErroTransmissao, PedidoTransmissao
from auxiliares import FULANO, PublicadorFalso, parear
from cliente.credenciais import CredenciaisMemoria
from cliente.estado import Estado


async def _esperar(condicao, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not await condicao():
            await asyncio.sleep(0.02)


async def test_sem_token_pede_pareamento_sem_tentar_conectar(backend, criar_cliente):
    cliente = criar_cliente()
    snapshot = await cliente.esperar(Estado.PRECISA_PAREAR)
    assert "/parear" in snapshot.mensagem
    assert backend.gateway.total_online == 0


async def test_pareia_guarda_token_e_mostra_identidade(backend, criar_cliente):
    cliente = criar_cliente()
    snapshot = await parear(backend, cliente)
    assert (snapshot.usuario, snapshot.dispositivo) == ("Fulano", "PC de teste")
    assert cliente.credenciais.ler(backend.url)
    assert backend.gateway.online(FULANO.discord_id)


async def test_reconecta_sozinho_com_o_token_salvo(backend, criar_cliente):
    primeiro = criar_cliente()
    await parear(backend, primeiro)
    await primeiro.fechar()

    segundo = criar_cliente(credenciais=primeiro.credenciais)
    snapshot = await segundo.esperar(Estado.ONLINE)
    assert snapshot.usuario == "Fulano"


async def test_codigo_invalido_volta_a_pedir_pareamento(backend, criar_cliente):
    cliente = criar_cliente()
    await cliente.esperar(Estado.PRECISA_PAREAR)
    cliente.controlador.parear("ZZZ-999")
    await cliente.esperar(Estado.CONECTANDO)
    snapshot = await cliente.esperar(Estado.PRECISA_PAREAR)
    assert "inválido" in snapshot.mensagem
    assert cliente.credenciais.ler(backend.url) is None


async def test_token_revogado_apaga_e_pede_pareamento(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)
    await cliente.fechar()
    # Parear de novo (outro PC) invalida o token antigo no backend.
    outro = criar_cliente()
    await parear(backend, outro)
    await outro.fechar()

    de_volta = criar_cliente(credenciais=cliente.credenciais)
    snapshot = await de_volta.esperar(Estado.PRECISA_PAREAR)
    assert "não é mais reconhecido" in snapshot.mensagem
    assert de_volta.credenciais.ler(backend.url) is None


async def test_duas_instancias_a_antiga_fica_bloqueada_sem_brigar(backend, criar_cliente):
    antiga = criar_cliente()
    await parear(backend, antiga)
    nova = criar_cliente(credenciais=antiga.credenciais)
    await nova.esperar(Estado.ONLINE)

    snapshot = await antiga.esperar(Estado.BLOQUEADO)
    assert "outro lugar" in snapshot.mensagem
    await asyncio.sleep(0.5)  # a bloqueada não pode ficar reconectando e derrubando a nova
    assert antiga.estado == Estado.BLOQUEADO and nova.estado == Estado.ONLINE


async def test_reconecta_sozinho_quando_o_backend_volta(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)

    await backend.desligar()
    await cliente.esperar(Estado.CONECTANDO)
    await backend.ligar()
    await cliente.esperar(Estado.ONLINE, timeout=5)


async def test_transmitir_aciona_o_publicador_com_os_parametros(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)

    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("dev", "1080p", 60, 6000, "h264"))
    snapshot = await cliente.esperar(Estado.TRANSMITINDO)

    iniciado = cliente.publicador.iniciados[0]
    assert iniciado.sala == "dev"
    p = iniciado.parametros
    assert (p.alvo_largura, p.alvo_altura, p.fps, p.bitrate_kbps, p.codec) == (1920, 1080, 60, 6000, "h264")
    assert (snapshot.sala, snapshot.resolucao, snapshot.codec) == ("dev", (1920, 1080), "h264")
    assert cliente.controlador.sessao_id == sessao.id


async def test_o_que_nao_foi_digitado_vem_dos_padroes_do_app(backend, criar_cliente):
    cliente = criar_cliente(resolucao="480p", fps=15, bitrate_kbps=900,
                            fonte={"tipo": "janela", "hwnd": 1, "titulo": "Jogo", "processo": "jogo.exe"})
    await parear(backend, cliente)

    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("dev", fps=60))  # só o fps digitado
    await cliente.esperar(Estado.TRANSMITINDO)
    iniciado = cliente.publicador.iniciados[0]
    p = iniciado.parametros
    assert (p.alvo_largura, p.alvo_altura, p.fps, p.bitrate_kbps) == (854, 480, 60, 900)
    assert iniciado.fonte["processo"] == "jogo.exe"
    # O Discord fica sabendo do que foi realmente aplicado.
    gravada = await backend.repo.sessao(sessao.id)
    assert (gravada.resolucao, gravada.fps, gravada.bitrate_kbps) == ("854x480", 60, 900)
    assert sessao.resolucao == "854x480"  # o /transmitir já responde com os valores reais


async def test_aplicar_agora_troca_sem_mudar_a_sessao_e_avisa_o_backend(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)
    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.esperar(Estado.TRANSMITINDO)

    cliente.controlador.config.resolucao, cliente.controlador.config.fps = "1080p", 60
    cliente.controlador.config.fonte = {"tipo": "tela", "esquerda": 0, "topo": 0, "largura": 1920, "altura": 1080}
    await cliente.controlador.aplicar_configuracao()

    troca = cliente.publicador.trocas[0]
    assert (troca.parametros.alvo_largura, troca.parametros.fps) == (1920, 60)
    assert cliente.controlador.sessao_id == sessao.id and cliente.publicador.paradas == 0
    await _esperar(lambda: _resolucao_gravada(backend, sessao.id, "1920x1080"))
    assert cliente.snapshots[-1].resolucao == (1920, 1080)


async def test_aplicar_agora_com_fonte_quebrada_encerra_e_avisa(backend, criar_cliente):
    cliente = criar_cliente(publicador=PublicadorFalso(falhar_na_troca="a janela não está aberta"))
    await parear(backend, cliente)
    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.esperar(Estado.TRANSMITINDO)

    await cliente.controlador.aplicar_configuracao()
    snapshot = await cliente.esperar(Estado.ONLINE)
    assert "não está aberta" in snapshot.mensagem
    await _esperar(lambda: _estado_sessao(backend, sessao.id, "encerrada"))


async def test_falha_da_midia_chega_ao_discord_com_o_motivo(backend, criar_cliente):
    cliente = criar_cliente(publicador=PublicadorFalso(falhar_com="monitor desconectado"))
    await parear(backend, cliente)
    with pytest.raises(ErroTransmissao, match="monitor desconectado"):
        await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    assert cliente.estado == Estado.ONLINE


async def test_parar_pelo_discord(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)
    await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.esperar(Estado.TRANSMITINDO)

    assert (await backend.servico.parar(FULANO)).confirmado
    snapshot = await cliente.esperar(Estado.ONLINE)
    assert cliente.publicador.paradas == 1 and snapshot.sala is None


async def test_parar_pela_bandeja_avisa_o_backend(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)
    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.esperar(Estado.TRANSMITINDO)

    await cliente.controlador.parar_local()
    await _esperar(lambda: _estado_sessao(backend, sessao.id, "encerrada"))
    assert (await backend.repo.sessao(sessao.id)).detalhe == "parado pelo usuário no PC"


async def test_queda_da_midia_encerra_e_avisa_o_backend(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)
    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.esperar(Estado.TRANSMITINDO)

    cliente.publicador.ao_cair("conexão com o LiveKit caiu (teste)")
    await cliente.esperar(Estado.ONLINE)
    await _esperar(lambda: _estado_sessao(backend, sessao.id, "encerrada"))


async def test_backend_cai_durante_a_transmissao_e_a_sessao_sobrevive(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)
    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.esperar(Estado.TRANSMITINDO)

    await backend.desligar()
    await _esperar_mensagem(cliente, "Sem conexão")
    assert cliente.estado == Estado.TRANSMITINDO  # o vídeo segue no ar sem o backend
    await backend.ligar()

    # Ao voltar, o 'hello' informa a sessão ativa e o backend a mantém.
    await _esperar(lambda: _online(backend))
    await _esperar(lambda: _estado_sessao(backend, sessao.id, "ativa"))
    assert cliente.publicador.paradas == 0


async def test_novo_iniciar_com_transmissao_ativa_troca_de_sessao(backend, criar_cliente):
    cliente = criar_cliente()
    await parear(backend, cliente)
    await backend.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.esperar(Estado.TRANSMITINDO)
    # O backend perdeu a sessão (ex.: banco restaurado) e pede outra.
    await backend.repo.encerrar_sessoes_do_usuario(FULANO.discord_id, "teste")

    nova = await backend.servico.transmitir(FULANO, PedidoTransmissao("outra"))
    async with asyncio.timeout(3):
        while cliente.controlador.sessao_id != nova.id:
            await asyncio.sleep(0.02)
    assert cliente.publicador.paradas == 1 and len(cliente.publicador.iniciados) == 2


async def _estado_sessao(backend, sessao_id: str, estado: str) -> bool:
    return (await backend.repo.sessao(sessao_id)).estado == estado


async def _resolucao_gravada(backend, sessao_id: str, resolucao: str) -> bool:
    return (await backend.repo.sessao(sessao_id)).resolucao == resolucao


async def _online(backend) -> bool:
    return backend.gateway.online(FULANO.discord_id)


async def _esperar_mensagem(cliente, trecho: str) -> None:
    async with asyncio.timeout(5):
        while trecho not in (cliente.snapshots[-1].mensagem if cliente.snapshots else ""):
            await asyncio.sleep(0.02)


def test_credenciais_memoria_isola_por_backend():
    cred = CredenciaisMemoria()
    cred.salvar("ws://a", "t1")
    assert cred.ler("ws://b") is None
