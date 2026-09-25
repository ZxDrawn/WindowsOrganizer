"""Motor: vigia a pasta, identifica, valida, renomeia, move e registra tudo no histórico."""
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import string
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import ai, db
from .config import EXTENSOES_DOCUMENTO, EXTENSOES_TEMPORARIAS, load_settings, load_types, save_settings
from .rules import Problema, tipar_campos, validar

log = logging.getLogger(__name__)

INVALIDOS_NOME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _ignorado(path: Path) -> bool:
    nome = path.name.lower()
    return (nome.startswith((".", "~$")) or nome in {"desktop.ini", "thumbs.db"}
            or path.suffix.lower() in EXTENSOES_TEMPORARIAS)


def _limpar_nome(nome: str) -> str:
    nome = INVALIDOS_NOME.sub("-", nome)
    nome = re.sub(r"\s+", " ", nome).strip(" .-")
    return nome[:150] or "Documento"


def _destino_livre(pasta: Path, nome: str, ext: str) -> Path:
    alvo = pasta / f"{nome}{ext}"
    n = 2
    while alvo.exists():
        alvo = pasta / f"{nome} ({n}){ext}"
        n += 1
    return alvo


class _Formatador(string.Formatter):
    """Monta o nome do arquivo a partir do padrão; campos faltando viram erro."""

    def get_value(self, key, args, kwargs):
        valor = kwargs.get(key)
        if valor in (None, ""):
            raise KeyError(key)
        return valor


class Organizer:
    def __init__(self, notify: Callable[[str, str], None] | None = None):
        self.notify = notify or (lambda titulo, msg: None)
        self.fila: queue.Queue[Path] = queue.Queue()
        self._na_fila: set[str] = set()
        self._proprios: dict[str, float] = {}   # caminhos que nós mesmos movemos (não logar como ação do usuário)
        self._origens: dict[str, float] = {}
        self._falhas: set[str] = set()          # já registramos erro para este arquivo
        self._lock = threading.Lock()
        self._observer: Observer | None = None
        self._parar = threading.Event()
        self.pausado = False
        self.root = Path(load_settings()["pasta_monitorada"])

    # ------------------------------------------------------------ ciclo de vida

    def start(self) -> None:
        self._parar.clear()
        self.root = Path(load_settings()["pasta_monitorada"])
        self.root.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._worker, daemon=True, name="organizer-worker").start()
        threading.Thread(target=self._varredura_periodica, daemon=True, name="organizer-scan").start()
        self._observer = Observer()
        self._observer.schedule(_Handler(self), str(self.root), recursive=True)
        self._observer.start()
        log.info("Monitorando %s", self.root)

    def stop(self) -> None:
        self._parar.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

    def restart(self) -> None:
        self.stop()
        self.start()

    # ------------------------------------------------------------ fila

    def enqueue(self, path: Path) -> None:
        chave = os.path.normcase(str(path))
        with self._lock:
            if chave in self._na_fila:
                return
            self._na_fila.add(chave)
        self.fila.put(path)

    def _worker(self) -> None:
        while not self._parar.is_set():
            try:
                path = self.fila.get(timeout=1)
            except queue.Empty:
                continue
            while self.pausado and not self._parar.is_set():
                time.sleep(1)
            try:
                self._processar(path)
            except Exception as e:
                log.exception("Falha ao processar %s", path)
                db.log_event("erro", path.name, str(path), observacao=str(e))
            finally:
                with self._lock:
                    self._na_fila.discard(os.path.normcase(str(path)))

    # ------------------------------------------------------------ nossos próprios movimentos

    def _marcar_proprio(self, origem: Path, destino: Path) -> None:
        expira = time.time() + 15
        with self._lock:
            self._proprios[os.path.normcase(str(origem))] = expira
            self._proprios[os.path.normcase(str(destino))] = expira
            self._origens[os.path.normcase(str(origem))] = expira

    def eh_proprio(self, caminho: str, so_origem: bool = False) -> bool:
        """True se o evento foi causado por um movimento feito pelo próprio programa."""
        agora = time.time()
        with self._lock:
            self._proprios = {k: v for k, v in self._proprios.items() if v > agora}
            self._origens = {k: v for k, v in self._origens.items() if v > agora}
            return os.path.normcase(caminho) in (self._origens if so_origem else self._proprios)

    # ------------------------------------------------------------ processamento

    def _esperar_estabilizar(self, path: Path, limite: float = 600) -> bool:
        """Espera o download/cópia terminar (tamanho parado e arquivo liberado)."""
        fim = time.time() + limite
        ultimo = -1
        while time.time() < fim:
            try:
                tamanho = path.stat().st_size
                with open(path, "rb"):
                    pass
            except FileNotFoundError:
                return False
            except PermissionError:
                time.sleep(1.5)
                continue
            if tamanho == ultimo and tamanho > 0:
                return True
            ultimo = tamanho
            time.sleep(1.5)
        return path.exists()

    def _processar(self, path: Path) -> None:
        if _ignorado(path) or not self._esperar_estabilizar(path):
            return
        caminho = str(path)
        hash_ = file_hash(path)
        tamanho = path.stat().st_size

        if path.suffix.lower() not in EXTENSOES_DOCUMENTO:
            db.track(caminho, hash_, tamanho)
            db.log_event("novo", path.name, caminho, hash_=hash_)
            return

        tipos = load_types()
        # O cache depende também dos tipos cadastrados: se você criar um tipo novo, o arquivo é reavaliado.
        chave_cache = hashlib.sha256((hash_ + json.dumps(tipos, sort_keys=True)).encode()).hexdigest()
        resultado = db.cache_get(chave_cache)
        if resultado is None:
            try:
                resultado = ai.classify(path, tipos)
            except ai.AIError as e:
                # Não marca como conhecido: a próxima varredura tenta de novo.
                if caminho not in self._falhas:
                    self._falhas.add(caminho)
                    db.log_event("erro", path.name, caminho, observacao=str(e), hash_=hash_)
                    self.notify("Não consegui analisar um arquivo", f"{path.name}: {e}")
                return
            db.cache_put(chave_cache, resultado)
        self._falhas.discard(caminho)

        tipo = next((t for t in tipos if t["id"] == resultado["tipo_id"]), None)
        resumo = resultado.get("resumo")

        if tipo is None:
            db.track(caminho, hash_, tamanho)
            db.log_event("nao_reconhecido", path.name, caminho, resumo=resumo, hash_=hash_)
            return

        if resultado["confianca"] == "baixa":
            destino = self._mover(path, self.root / load_settings()["pasta_revisar"], path.stem)
            db.track(str(destino), hash_, tamanho)
            db.log_event("revisar", path.name, caminho, str(destino), tipo["nome"], resultado["campos"], resumo,
                         "IA com pouca certeza sobre o tipo do documento.", hash_)
            self.notify("Documento para revisar", f"{path.name} (talvez {tipo['nome']})")
            return

        campos = tipar_campos(tipo, resultado["campos"])
        problemas = validar(tipo, campos)
        try:
            novo_nome = _Formatador().format(tipo["padrao_nome"], **campos)
        except (KeyError, ValueError, IndexError) as e:
            novo_nome = f"{tipo['nome']} - {path.stem}"
            problemas.append(Problema(f"Campo não encontrado para montar o nome: {e}", "DADOS INCOMPLETOS"))

        sufixos = list(dict.fromkeys(p.sufixo for p in problemas))
        if sufixos:
            novo_nome += " - " + " - ".join(sufixos)
        destino = self._mover(path, self.root / tipo["pasta_destino"], _limpar_nome(novo_nome))

        db.track(str(destino), hash_, tamanho)
        campos_log = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in campos.items()}
        db.log_event("organizado", path.name, caminho, str(destino), tipo["nome"], campos_log, resumo,
                     "; ".join(p.mensagem for p in problemas) or None, hash_)
        titulo = f"{tipo['nome']} organizado" + (" (COM PROBLEMA)" if problemas else "")
        self.notify(titulo, destino.name)

    def _mover(self, origem: Path, pasta: Path, nome: str) -> Path:
        pasta.mkdir(parents=True, exist_ok=True)
        destino = _destino_livre(pasta, nome, origem.suffix.lower())
        self._marcar_proprio(origem, destino)
        shutil.move(str(origem), str(destino))
        db.untrack(str(origem))
        return destino

    # ------------------------------------------------------------ eventos do usuário (vindos do watchdog)

    def on_novo(self, path: Path) -> None:
        if _ignorado(path) or self.eh_proprio(str(path)):
            return
        if path.parent == self.root:
            self.enqueue(path)
        elif not db.is_tracked(str(path)):
            # Arquivo colocado direto numa subpasta: só registra.
            try:
                db.track(str(path), None, path.stat().st_size)
                db.log_event("novo", path.name, str(path))
            except FileNotFoundError:
                pass

    def on_apagado(self, caminho: str, eh_pasta: bool) -> None:
        # Só a origem de um movimento nosso "some"; o destino, se sumir, foi o usuário que apagou.
        if self.eh_proprio(caminho, so_origem=True):
            return
        alvos = [caminho]
        if eh_pasta or not db.is_tracked(caminho):
            prefixo = os.path.normcase(caminho.rstrip("\\/") + os.sep)
            alvos = [p for p in db.tracked_paths() if os.path.normcase(p).startswith(prefixo)] or alvos
        for alvo in alvos:
            if db.is_tracked(alvo):
                db.untrack(alvo)
                db.log_event("apagado", Path(alvo).name, alvo)

    def on_movido(self, origem: str, destino: str) -> None:
        if self.eh_proprio(origem) or self.eh_proprio(destino):
            return
        src, dst = Path(origem), Path(destino)
        if _ignorado(src) or not db.is_tracked(origem):
            # Ex.: navegador renomeando "arquivo.pdf.crdownload" -> "arquivo.pdf" no fim do download.
            self.on_novo(dst)
            return
        db.retrack(origem, destino)
        evento = "renomeado" if src.parent == dst.parent else "movido"
        db.log_event(evento, src.name, origem, destino)

    # ------------------------------------------------------------ varredura

    def _varredura_periodica(self) -> None:
        self.varrer()
        while not self._parar.wait(max(1, load_settings()["varredura_minutos"]) * 60):
            self.varrer()

    def varrer(self) -> None:
        """Compara a pasta com o histórico: pega o que mudou enquanto o programa estava fechado."""
        settings = load_settings()
        existentes = {str(p) for p in self.root.rglob("*") if p.is_file() and not _ignorado(p)}
        conhecidos = db.tracked_paths()

        if not settings.get("inventario_feito"):
            # Primeira execução: não mexe no que já estava lá, só registra.
            for c in existentes:
                db.track(c, None, os.path.getsize(c))
            db.log_event("inventario", str(self.root), str(self.root),
                         observacao=f"Inventário inicial: {len(existentes)} arquivos já existentes (não foram alterados).")
            settings["inventario_feito"] = True
            save_settings(settings)
            return

        for c in conhecidos - existentes:
            if not os.path.exists(c):
                db.untrack(c)
                db.log_event("apagado", Path(c).name, c, observacao="Detectado na varredura.")
        for c in existentes - conhecidos:
            self.on_novo(Path(c))

    def organizar_existentes(self) -> int:
        """Manda para a IA os documentos que estão soltos na raiz da pasta."""
        n = 0
        for p in self.root.iterdir():
            if p.is_file() and not _ignorado(p) and p.suffix.lower() in EXTENSOES_DOCUMENTO:
                self.enqueue(p)
                n += 1
        return n


class _Handler(FileSystemEventHandler):
    def __init__(self, org: Organizer):
        self.org = org

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.org.on_novo(Path(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        self.org.on_apagado(event.src_path, event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.org.on_movido(event.src_path, event.dest_path)
