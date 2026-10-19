import base64
import io
import json
import urllib.error

import pytest

from organizer import ai


def configurar(monkeypatch, **extra):
    base = {"motor": "local", "modelo_local": "modelo-x", "url_ollama": "http://ollama.teste/",
            "contexto_local": 4096, "max_paginas_pdf": 6}
    monkeypatch.setattr(ai, "load_settings", lambda: {**base, **extra})


class RespostaFalsa(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def servidor_falso(monkeypatch, resposta: dict | None = None, erro: Exception | None = None):
    enviados = []

    def urlopen(pedido, timeout=None):
        enviados.append((pedido.full_url, json.loads(pedido.data)))
        if erro:
            raise erro
        return RespostaFalsa(json.dumps(resposta).encode())

    monkeypatch.setattr(ai.urllib.request, "urlopen", urlopen)
    return enviados


def test_para_ollama_separa_texto_e_imagens():
    content = [{"type": "text", "text": "um"},
               {"type": "image", "source": {"data": "AAAA"}},
               {"type": "text", "text": "dois"}]
    assert ai._para_ollama(content) == ("um\n\ndois", ["AAAA"])


def test_pdf_escaneado_vira_imagens_para_a_ia_local():
    import pymupdf
    doc = pymupdf.open()
    for _ in range(5):
        doc.new_page()
    pdf = base64.standard_b64encode(doc.tobytes()).decode()
    _, imagens = ai._para_ollama([{"type": "document", "source": {"data": pdf}}])
    assert len(imagens) == ai.MAX_PAGINAS_ESCANEADAS
    assert base64.b64decode(imagens[0]).startswith(b"\x89PNG")


def test_chamada_local_monta_pedido_e_le_resposta(monkeypatch):
    configurar(monkeypatch)
    enviados = servidor_falso(monkeypatch, {"message": {"content": '{"tipo_id": "folha_ponto"}'}})
    schema = {"type": "object"}
    r = ai._call("instruções", [{"type": "text", "text": "olá"}, {"type": "image", "source": {"data": "AAAA"}}], schema)

    assert r == {"tipo_id": "folha_ponto"}
    url, corpo = enviados[0]
    assert url == "http://ollama.teste/api/chat"
    assert corpo["model"] == "modelo-x" and corpo["format"] == schema and corpo["stream"] is False
    assert corpo["options"] == {"temperature": 0, "num_ctx": 4096}
    assert corpo["messages"][0] == {"role": "system", "content": "instruções"}
    assert corpo["messages"][1]["images"] == ["AAAA"]


def test_motor_claude_nao_usa_ollama(monkeypatch):
    configurar(monkeypatch, motor="claude")
    monkeypatch.setattr(ai, "_call_claude", lambda *a: {"via": "claude"})
    monkeypatch.setattr(ai, "_call_local", lambda *a: pytest.fail("não devia chamar o Ollama"))
    assert ai._call("s", [], {}) == {"via": "claude"}


def test_ollama_fechado_da_mensagem_clara(monkeypatch):
    configurar(monkeypatch)
    servidor_falso(monkeypatch, erro=urllib.error.URLError("recusado"))
    with pytest.raises(ai.AIError, match="Ollama"):
        ai._call("s", [{"type": "text", "text": "x"}], {})


def test_modelo_nao_instalado(monkeypatch):
    configurar(monkeypatch)
    erro = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"model not found"))
    servidor_falso(monkeypatch, erro=erro)
    with pytest.raises(ai.AIError, match="ollama pull modelo-x"):
        ai._call("s", [{"type": "text", "text": "x"}], {})


@pytest.mark.parametrize("conteudo", ["", "isso não é json"])
def test_resposta_vazia_ou_fora_do_formato(monkeypatch, conteudo):
    configurar(monkeypatch)
    servidor_falso(monkeypatch, {"message": {"content": conteudo}})
    with pytest.raises(ai.AIError):
        ai._call("s", [{"type": "text", "text": "x"}], {})
