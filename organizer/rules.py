"""Regras de validação. A IA só extrai os dados; quem confere é este código, de forma determinística."""
import calendar
from dataclasses import dataclass
from datetime import date


class Mes(date):
    """Data que, no nome do arquivo, aparece como AAAA-MM por padrão ({mes:%m-%Y} também funciona)."""

    def __format__(self, spec: str) -> str:
        return super().__format__(spec or "%Y-%m")


@dataclass
class Problema:
    mensagem: str   # vai para o histórico
    sufixo: str     # vai para o nome do arquivo


def parse_date(valor) -> date | None:
    if isinstance(valor, date):
        return valor
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except ValueError:
        return None


def parse_mes(valor) -> Mes | None:
    try:
        ano, mes = str(valor).strip()[:7].split("-")
        return Mes(int(ano), int(mes), 1)
    except (ValueError, AttributeError):
        return None


def tipar_campos(tipo: dict, campos: dict) -> dict:
    """Converte os valores de texto da IA em datas/meses conforme o tipo do campo."""
    tipos = {c["chave"]: c.get("tipo", "texto") for c in tipo["campos"]}
    saida = {}
    for chave, valor in campos.items():
        if tipos.get(chave) == "data":
            saida[chave] = parse_date(valor) or valor
        elif tipos.get(chave) == "mes":
            saida[chave] = parse_mes(valor) or valor
        else:
            saida[chave] = valor
    return saida


def _dia(ano: int, mes: int, dia: int) -> date:
    # "Dia 31" em fevereiro vira o último dia do mês.
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def _mes_anterior(d: date) -> tuple[int, int]:
    return (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)


def _periodo_referencia(regra: dict, campos: dict) -> list[Problema]:
    sufixo = regra.get("sufixo_erro") or "DATA ERRADA"
    inicio = campos.get(regra["campo_inicio"])
    fim = campos.get(regra["campo_fim"])
    if not isinstance(inicio, date) or not isinstance(fim, date):
        return [Problema("Período não encontrado no documento.", sufixo)]

    campos["mes_referencia"] = Mes(fim.year, fim.month, 1)
    dia_ini, dia_fim = regra.get("dia_inicio"), regra.get("dia_fim")
    if not dia_ini or not dia_fim:
        return []

    fim_esperado = _dia(fim.year, fim.month, dia_fim)
    # Se começa num dia maior que o do fim (ex.: 19 → 18), o início é no mês anterior.
    ano_i, mes_i = _mes_anterior(fim) if dia_ini > dia_fim else (fim.year, fim.month)
    inicio_esperado = _dia(ano_i, mes_i, dia_ini)

    problemas = []
    if fim != fim_esperado:
        problemas.append(Problema(f"Fim do período é {fim:%d/%m/%Y}, esperado dia {dia_fim}.", sufixo))
        # Se só o fim está errado, o mês de referência vem do início.
        if inicio.day == dia_ini:
            ano, mes = inicio.year, inicio.month
            if dia_ini > dia_fim:
                ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
            campos["mes_referencia"] = Mes(ano, mes, 1)
            inicio_esperado = inicio
    if inicio != inicio_esperado:
        problemas.append(Problema(
            f"Início do período é {inicio:%d/%m/%Y}, esperado {inicio_esperado:%d/%m/%Y}.", sufixo))
    return problemas


def _data_nao_futura(regra: dict, campos: dict) -> list[Problema]:
    valor = campos.get(regra["campo"])
    if isinstance(valor, date) and valor > date.today():
        return [Problema(f"{regra['campo']} ({valor:%d/%m/%Y}) está no futuro.",
                         regra.get("sufixo_erro") or "DATA NO FUTURO")]
    return []


REGRAS = {
    "periodo_referencia": _periodo_referencia,
    "data_nao_futura": _data_nao_futura,
}


def validar(tipo: dict, campos: dict) -> list[Problema]:
    """Aplica as regras do tipo. Pode acrescentar campos calculados (ex.: mes_referencia) em `campos`."""
    problemas = []
    for regra in tipo.get("regras", []):
        fn = REGRAS.get(regra.get("tipo"))
        if fn:
            problemas += fn(regra, campos)
    return problemas
