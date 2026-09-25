"""Configurações do WindowsOrganizer e tipos de documento cadastrados."""
import json
import os
import threading
from pathlib import Path

APP_NAME = "WindowsOrganizer"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "historico.db"
SETTINGS_PATH = DATA_DIR / "config.json"
TYPES_PATH = DATA_DIR / "tipos.json"
LOG_PATH = DATA_DIR / "app.log"

DEFAULT_SETTINGS = {
    "pasta_monitorada": str(Path.home() / "Downloads"),
    "modelo": "claude-opus-5",
    # Arquivos que a IA não reconhece (ou reconhece com pouca confiança) vão para cá.
    "pasta_revisar": "_A revisar",
    "varredura_minutos": 10,
    "max_paginas_pdf": 6,
}

# Extensões lidas pela IA. O resto é apenas registrado no histórico.
EXTENSOES_DOCUMENTO = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
# Downloads em andamento / arquivos temporários: ignorados.
EXTENSOES_TEMPORARIAS = {".crdownload", ".part", ".tmp", ".download", ".partial"}

# Tipos que já vêm de fábrica. O usuário pode editar ou apagar.
DEFAULT_TYPES = [
    {
        "id": "folha_ponto",
        "nome": "Folha de ponto",
        "descricao": "Folha/espelho de ponto de um funcionário, com registros de entrada e saída "
                     "dia a dia em um período de aproximadamente um mês.",
        "instrucoes": "O período normalmente aparece no cabeçalho (ex.: 'Período: 19/07/2026 a 18/08/2026').",
        "campos": [
            {"chave": "nome", "descricao": "Nome completo do funcionário", "tipo": "texto"},
            {"chave": "data_inicio", "descricao": "Primeiro dia do período da folha", "tipo": "data"},
            {"chave": "data_fim", "descricao": "Último dia do período da folha", "tipo": "data"},
        ],
        "padrao_nome": "{nome} - {mes_referencia}",
        "pasta_destino": "Folhas de Ponto",
        "regras": [
            {
                "tipo": "periodo_referencia",
                "campo_inicio": "data_inicio",
                "campo_fim": "data_fim",
                "dia_inicio": 19,
                "dia_fim": 18,
                "sufixo_erro": "FOLHA COM DATA ERRADA",
            }
        ],
    },
    {
        "id": "atestado",
        "nome": "Atestado médico",
        "descricao": "Atestado médico ou odontológico (foto ou PDF), emitido por um profissional de "
                     "saúde, justificando ausência de uma pessoa.",
        "instrucoes": "Pode ser manuscrito. A data do atestado é a data de emissão/atendimento.",
        "campos": [
            {"chave": "nome", "descricao": "Nome do paciente", "tipo": "texto"},
            {"chave": "data", "descricao": "Data do atestado", "tipo": "data"},
            {"chave": "dias_afastamento", "descricao": "Quantidade de dias de afastamento, se houver", "tipo": "texto"},
        ],
        "padrao_nome": "{nome} - {data}",
        "pasta_destino": "Atestados",
        "regras": [
            {"tipo": "data_nao_futura", "campo": "data", "sufixo_erro": "DATA NO FUTURO"}
        ],
    },
]

_lock = threading.Lock()


def _read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_settings() -> dict:
    with _lock:
        return {**DEFAULT_SETTINGS, **_read_json(SETTINGS_PATH, {})}


def save_settings(settings: dict) -> None:
    with _lock:
        _write_json(SETTINGS_PATH, settings)


def load_types() -> list[dict]:
    with _lock:
        if not TYPES_PATH.exists():
            _write_json(TYPES_PATH, DEFAULT_TYPES)
        return _read_json(TYPES_PATH, [])


def save_types(types: list[dict]) -> None:
    with _lock:
        _write_json(TYPES_PATH, types)
