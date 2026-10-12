"""O cliente espelha o protocolo do backend. Se alguém mudar só um lado, este teste quebra."""

from __future__ import annotations

import inspect

from pydantic import BaseModel

import app.protocolo as backend
import cliente.protocolo as cliente


def _modelos(modulo) -> dict[str, type[BaseModel]]:
    return {nome: obj for nome, obj in inspect.getmembers(modulo, inspect.isclass)
            if issubclass(obj, BaseModel) and obj.__module__ == modulo.__name__ and not nome.startswith("_")}


def test_mesmos_modelos_com_o_mesmo_schema():
    do_backend, do_cliente = _modelos(backend), _modelos(cliente)
    assert set(do_backend) == set(do_cliente)
    for nome, modelo in do_backend.items():
        assert modelo.model_json_schema() == do_cliente[nome].model_json_schema(), nome


def test_mesma_versao_e_codigos_de_fechamento():
    assert backend.VERSAO == cliente.VERSAO
    assert {f.name: f.value for f in backend.Fechamento} == {f.name: f.value for f in cliente.Fechamento}
