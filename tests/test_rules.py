from datetime import date, timedelta

from organizer.config import DEFAULT_TYPES
from organizer.rules import Mes, parse_date, parse_mes, tipar_campos, validar

FOLHA = next(t for t in DEFAULT_TYPES if t["id"] == "folha_ponto")
ATESTADO = next(t for t in DEFAULT_TYPES if t["id"] == "atestado")


def folha(inicio, fim):
    campos = tipar_campos(FOLHA, {"nome": "João Silva", "data_inicio": inicio, "data_fim": fim})
    return campos, validar(FOLHA, campos)


# ------------------------------------------------------------ conversões

def test_parse_date():
    assert parse_date("2026-08-18") == date(2026, 8, 18)
    assert parse_date(" 2026-08-18T10:00 ") == date(2026, 8, 18)
    assert parse_date(date(2026, 1, 1)) == date(2026, 1, 1)
    assert parse_date("18/08/2026") is None
    assert parse_date(None) is None
    assert parse_date("") is None


def test_parse_mes_e_formato():
    mes = parse_mes("2026-08")
    assert mes == date(2026, 8, 1)
    assert f"{mes}" == "2026-08"
    assert f"{mes:%m-%Y}" == "08-2026"
    assert parse_mes("agosto") is None
    assert parse_mes(None) is None


def test_tipar_campos_converte_so_datas_e_mantem_invalidos():
    campos = tipar_campos(FOLHA, {"nome": "Ana", "data_inicio": "2026-07-19", "data_fim": "ilegível"})
    assert campos["nome"] == "Ana"
    assert campos["data_inicio"] == date(2026, 7, 19)
    assert campos["data_fim"] == "ilegível"


# ------------------------------------------------------------ período de referência

def test_folha_correta_nao_tem_problemas_e_calcula_mes():
    campos, problemas = folha("2026-07-19", "2026-08-18")
    assert problemas == []
    assert campos["mes_referencia"] == Mes(2026, 8, 1)


def test_folha_virada_de_ano():
    campos, problemas = folha("2025-12-19", "2026-01-18")
    assert problemas == []
    assert campos["mes_referencia"] == Mes(2026, 1, 1)


def test_folha_do_mes_inteiro_acusa_inicio_e_fim():
    # Exemplo do README: período 01/07 a 31/07.
    campos, problemas = folha("2026-07-01", "2026-07-31")
    assert len(problemas) == 2
    assert "esperado dia 18" in problemas[0].mensagem
    assert "esperado 19/06/2026" in problemas[1].mensagem
    assert {p.sufixo for p in problemas} == {"FOLHA COM DATA ERRADA"}
    assert campos["mes_referencia"] == Mes(2026, 7, 1)


def test_so_o_fim_errado_usa_mes_do_inicio():
    campos, problemas = folha("2026-07-19", "2026-08-20")
    assert len(problemas) == 1
    assert "Fim do período" in problemas[0].mensagem
    assert campos["mes_referencia"] == Mes(2026, 8, 1)


def test_so_o_fim_errado_com_inicio_em_dezembro():
    campos, problemas = folha("2025-12-19", "2026-01-25")
    assert len(problemas) == 1
    assert campos["mes_referencia"] == Mes(2026, 1, 1)


def test_so_o_inicio_errado():
    _, problemas = folha("2026-07-15", "2026-08-18")
    assert len(problemas) == 1
    assert "Início do período é 15/07/2026, esperado 19/07/2026" in problemas[0].mensagem


def test_periodo_ausente_ou_ilegivel():
    for inicio, fim in [(None, "2026-08-18"), ("2026-07-19", None), ("xx", "yy")]:
        campos, problemas = folha(inicio, fim)
        assert [p.mensagem for p in problemas] == ["Período não encontrado no documento."]
        assert "mes_referencia" not in campos


def test_dia_31_em_fevereiro_vira_ultimo_dia():
    tipo = {"regras": [{"tipo": "periodo_referencia", "campo_inicio": "i", "campo_fim": "f",
                        "dia_inicio": 1, "dia_fim": 31}],
            "campos": [{"chave": "i", "tipo": "data"}, {"chave": "f", "tipo": "data"}]}
    campos = tipar_campos(tipo, {"i": "2026-02-01", "f": "2026-02-28"})
    assert validar(tipo, campos) == []


def test_sufixo_padrao_quando_regra_nao_define():
    tipo = {"regras": [{"tipo": "periodo_referencia", "campo_inicio": "i", "campo_fim": "f"}],
            "campos": [{"chave": "i", "tipo": "data"}, {"chave": "f", "tipo": "data"}]}
    problemas = validar(tipo, tipar_campos(tipo, {"i": None, "f": None}))
    assert problemas[0].sufixo == "DATA ERRADA"


# ------------------------------------------------------------ data não futura

def atestado(data):
    return validar(ATESTADO, tipar_campos(ATESTADO, {"nome": "Pedro", "data": data}))


def test_data_nao_futura():
    hoje = date.today()
    assert atestado(hoje.isoformat()) == []
    assert atestado((hoje - timedelta(days=30)).isoformat()) == []
    problemas = atestado((hoje + timedelta(days=1)).isoformat())
    assert len(problemas) == 1
    assert problemas[0].sufixo == "DATA NO FUTURO"


def test_data_ilegivel_nao_gera_problema_de_futuro():
    assert atestado(None) == []


def test_regra_desconhecida_e_ignorada():
    assert validar({"regras": [{"tipo": "inexistente"}]}, {}) == []
    assert validar({}, {}) == []
