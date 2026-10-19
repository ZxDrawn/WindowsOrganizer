"""Isola os testes: dados do app vão para uma pasta temporária, nunca para o %LOCALAPPDATA% real."""
import os
import sys
import tempfile
from pathlib import Path

# Precisa acontecer antes de importar `organizer` (config.py e db.py criam arquivos ao importar).
_DADOS = tempfile.mkdtemp(prefix="wo_testes_")
os.environ["LOCALAPPDATA"] = _DADOS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from organizer import ai, config, db, engine  # noqa: E402


@pytest.fixture(autouse=True)
def banco_limpo():
    with db._lock:
        for tabela in ("eventos", "arquivos", "cache_ia"):
            db._conn.execute(f"DELETE FROM {tabela}")
        db._conn.commit()


@pytest.fixture
def org(tmp_path, monkeypatch):
    """Organizer apontando para uma pasta temporária, sem espera de download e com tipos padrão."""
    settings = {**config.DEFAULT_SETTINGS, "pasta_monitorada": str(tmp_path / "Downloads")}
    monkeypatch.setattr(engine, "load_settings", lambda: dict(settings))
    monkeypatch.setattr(engine, "save_settings", lambda novo: settings.update(novo))
    monkeypatch.setattr(engine, "load_types", lambda: config.DEFAULT_TYPES)
    o = engine.Organizer()
    o.root.mkdir(parents=True)
    monkeypatch.setattr(o, "_esperar_estabilizar", lambda path, limite=600: path.exists())
    o.settings = settings
    return o


@pytest.fixture
def ia(monkeypatch):
    """Substitui a chamada ao Claude. Use `ia.resultado = {...}` ou `ia.erro = ai.AIError(...)`."""
    class FakeIA:
        resultado: dict = {}
        erro: Exception | None = None
        chamadas = 0

        def classify(self, path, tipos):
            self.chamadas += 1
            if self.erro:
                raise self.erro
            return dict(self.resultado)

    fake = FakeIA()
    monkeypatch.setattr(ai, "classify", fake.classify)
    return fake


def eventos(evento: str = "") -> list:
    return list(reversed(db.search_events(evento=evento)))
