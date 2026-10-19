from datetime import date, timedelta
from pathlib import Path

from organizer import ai, db
from organizer.engine import _Formatador, _destino_livre, _ignorado, _limpar_nome

from .conftest import eventos


def criar(org, nome, conteudo=b"conteudo") -> Path:
    p = org.root / nome
    p.write_bytes(conteudo)
    return p


def folha_ok(**extra):
    return {"tipo_id": "folha_ponto", "confianca": "alta", "resumo": "Folha de ponto",
            "campos": {"nome": "João Silva", "data_inicio": "2026-07-19", "data_fim": "2026-08-18", **extra}}


# ------------------------------------------------------------ funções auxiliares

def test_limpar_nome():
    assert _limpar_nome('a<b>c:d"e/f\\g|h?i*j') == "a-b-c-d-e-f-g-h-i-j"
    assert _limpar_nome("  Maria   da  Silva. ") == "Maria da Silva"
    assert _limpar_nome("...") == "Documento"
    assert len(_limpar_nome("x" * 500)) == 150


def test_ignorado():
    for nome in [".oculto", "~$doc.docx", "desktop.ini", "Thumbs.db", "a.pdf.crdownload", "b.tmp", "c.part"]:
        assert _ignorado(Path(nome)), nome
    assert not _ignorado(Path("relatorio.pdf"))


def test_destino_livre_numera_conflitos(tmp_path):
    assert _destino_livre(tmp_path, "Doc", ".pdf") == tmp_path / "Doc.pdf"
    (tmp_path / "Doc.pdf").write_bytes(b"1")
    assert _destino_livre(tmp_path, "Doc", ".pdf") == tmp_path / "Doc (2).pdf"
    (tmp_path / "Doc (2).pdf").write_bytes(b"1")
    assert _destino_livre(tmp_path, "Doc", ".pdf") == tmp_path / "Doc (3).pdf"


def test_formatador_falha_com_campo_ausente():
    import pytest
    assert _Formatador().format("{a} - {b}", a="x", b="y") == "x - y"
    for faltando in (None, ""):
        with pytest.raises(KeyError):
            _Formatador().format("{a} - {b}", a="x", b=faltando)


# ------------------------------------------------------------ processamento

def test_folha_correta_e_renomeada_e_movida(org, ia):
    ia.resultado = folha_ok()
    origem = criar(org, "espelho_ponto.pdf")
    org._processar(origem)

    destino = org.root / "Folhas de Ponto" / "João Silva - 2026-08.pdf"
    assert destino.exists() and not origem.exists()
    assert db.is_tracked(str(destino)) and not db.is_tracked(str(origem))
    (ev,) = eventos("organizado")
    assert ev["tipo"] == "Folha de ponto" and ev["observacao"] is None


def test_folha_com_data_errada_recebe_sufixo_e_motivo(org, ia):
    ia.resultado = folha_ok(data_inicio="2026-07-01", data_fim="2026-07-31", nome="Maria Souza")
    org._processar(criar(org, "export (2).pdf"))

    destino = org.root / "Folhas de Ponto" / "Maria Souza - 2026-07 - FOLHA COM DATA ERRADA.pdf"
    assert destino.exists()
    (ev,) = eventos("organizado")
    assert "esperado dia 18" in ev["observacao"]


def test_atestado_no_futuro(org, ia):
    futuro = (date.today() + timedelta(days=5)).isoformat()
    ia.resultado = {"tipo_id": "atestado", "confianca": "alta", "resumo": "",
                    "campos": {"nome": "Pedro Lima", "data": futuro, "dias_afastamento": "2"}}
    org._processar(criar(org, "IMG_0931.jpg"))
    assert (org.root / "Atestados" / f"Pedro Lima - {futuro} - DATA NO FUTURO.jpg").exists()


def test_dados_incompletos_usam_nome_alternativo(org, ia):
    ia.resultado = folha_ok(nome=None)
    org._processar(criar(org, "folha.pdf"))
    destino = org.root / "Folhas de Ponto" / "Folha de ponto - folha - DADOS INCOMPLETOS.pdf"
    assert destino.exists()
    assert "Campo não encontrado" in eventos("organizado")[0]["observacao"]


def test_pouca_certeza_vai_para_revisar(org, ia):
    ia.resultado = {**folha_ok(), "confianca": "baixa"}
    org._processar(criar(org, "duvida.pdf"))
    assert (org.root / "_A revisar" / "duvida.pdf").exists()
    assert len(eventos("revisar")) == 1


def test_tipo_nenhum_fica_no_lugar_e_registra_resumo(org, ia):
    ia.resultado = {"tipo_id": "nenhum", "confianca": "alta", "campos": {}, "resumo": "Boleto de energia elétrica"}
    origem = criar(org, "fatura.pdf")
    org._processar(origem)
    assert origem.exists() and db.is_tracked(str(origem))
    (ev,) = eventos("nao_reconhecido")
    assert ev["resumo"] == "Boleto de energia elétrica"


def test_arquivo_que_nao_e_documento_nao_vai_para_ia(org, ia):
    origem = criar(org, "setup.exe")
    org._processar(origem)
    assert ia.chamadas == 0
    assert origem.exists() and len(eventos("novo")) == 1


def test_temporarios_sao_ignorados(org, ia):
    org._processar(criar(org, "baixando.pdf.crdownload"))
    assert ia.chamadas == 0 and eventos() == []


def test_erro_da_ia_registra_uma_vez_e_tenta_de_novo_depois(org, ia):
    ia.erro = ai.AIError("Sem conexão com a internet.")
    origem = criar(org, "doc.pdf")
    org._processar(origem)
    org._processar(origem)
    assert len(eventos("erro")) == 1          # não repete o aviso
    assert origem.exists() and not db.is_tracked(str(origem))

    ia.erro, ia.resultado = None, folha_ok()
    org._processar(origem)                     # a "próxima varredura" funciona
    assert not origem.exists() and len(eventos("organizado")) == 1


def test_mesmo_conteudo_nao_chama_a_ia_duas_vezes(org, ia):
    ia.resultado = folha_ok()
    org._processar(criar(org, "a.pdf", b"mesmo"))
    org._processar(criar(org, "b.pdf", b"mesmo"))
    assert ia.chamadas == 1
    assert (org.root / "Folhas de Ponto" / "João Silva - 2026-08 (2).pdf").exists()


def test_nome_com_caracteres_invalidos_e_limpo(org, ia):
    ia.resultado = folha_ok(nome='Ana "Bia" / Costa')
    org._processar(criar(org, "f.pdf"))
    assert (org.root / "Folhas de Ponto" / "Ana -Bia- - Costa - 2026-08.pdf").exists()


# ------------------------------------------------------------ eventos do usuário

def test_movimento_proprio_nao_vira_evento_de_apagado(org, ia):
    ia.resultado = folha_ok()
    origem = criar(org, "f.pdf")
    org._processar(origem)
    org.on_apagado(str(origem), False)
    assert eventos("apagado") == []


def test_usuario_apagando_arquivo_organizado_e_registrado(org, ia):
    ia.resultado = folha_ok()
    org._processar(criar(org, "f.pdf"))
    destino = org.root / "Folhas de Ponto" / "João Silva - 2026-08.pdf"
    org.on_apagado(str(destino), False)
    (ev,) = eventos("apagado")
    assert ev["arquivo"] == destino.name and not db.is_tracked(str(destino))


def test_apagar_pasta_registra_arquivos_dentro_dela(org):
    pasta = org.root / "Atestados"
    for n in ("a.pdf", "b.pdf"):
        db.track(str(pasta / n), None, 1)
    org.on_apagado(str(pasta), True)
    assert len(eventos("apagado")) == 2 and db.tracked_paths() == set()


def test_renomear_e_mover_pelo_usuario(org):
    a = org.root / "Atestados" / "a.pdf"
    db.track(str(a), None, 1)
    org.on_movido(str(a), str(org.root / "Atestados" / "b.pdf"))
    org.on_movido(str(org.root / "Atestados" / "b.pdf"), str(org.root / "Outra" / "b.pdf"))
    assert [e["evento"] for e in eventos()] == ["renomeado", "movido"]
    assert db.tracked_paths() == {str(org.root / "Outra" / "b.pdf")}


def test_fim_de_download_vira_novo_arquivo_na_fila(org):
    parcial = org.root / "arq.pdf.crdownload"
    final = criar(org, "arq.pdf")
    org.on_movido(str(parcial), str(final))
    assert org.fila.get_nowait() == final


def test_arquivo_novo_na_raiz_entra_na_fila_sem_duplicar(org):
    p = criar(org, "novo.pdf")
    org.on_novo(p)
    org.on_novo(p)
    assert org.fila.qsize() == 1


def test_arquivo_novo_em_subpasta_so_registra(org):
    sub = org.root / "Outra"
    sub.mkdir()
    p = sub / "x.pdf"
    p.write_bytes(b"1")
    org.on_novo(p)
    assert org.fila.empty() and db.is_tracked(str(p)) and len(eventos("novo")) == 1


# ------------------------------------------------------------ varredura

def test_primeira_varredura_so_faz_inventario(org, ia):
    p = criar(org, "antigo.pdf")
    org.varrer()
    assert p.exists() and db.is_tracked(str(p))
    assert org.fila.empty() and ia.chamadas == 0
    assert len(eventos("inventario")) == 1 and org.settings["inventario_feito"]


def test_varredura_detecta_apagado_e_novo(org):
    antigo = criar(org, "antigo.pdf")
    org.varrer()
    antigo.unlink()
    novo = criar(org, "novo.pdf")
    org.varrer()
    assert len(eventos("apagado")) == 1
    assert org.fila.get_nowait() == novo


def test_organizar_existentes_enfileira_so_documentos_da_raiz(org):
    criar(org, "a.pdf"), criar(org, "b.jpg"), criar(org, "c.exe"), criar(org, ".oculto.pdf")
    (org.root / "sub").mkdir()
    (org.root / "sub" / "d.pdf").write_bytes(b"1")
    assert org.organizar_existentes() == 2
