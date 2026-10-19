"""Integração com o Claude: identificar documentos e aprender tipos novos a partir de exemplos."""
import base64
import io
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import anthropic
import keyring
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .config import APP_NAME, load_settings

log = logging.getLogger(__name__)

KEYRING_USER = "anthropic_api_key"
# Modelos que aceitam o fallback automático do servidor quando o pedido é recusado.
MODELOS_COM_FALLBACK = {"claude-opus-5", "claude-fable-5-1"}
MAX_IMAGEM_BYTES = 4_500_000
MAX_LADO_IMAGEM = 2400
MIN_CHARS_POR_PAGINA = 100
MAX_CHARS_TEXTO_PDF = 60_000
MAX_PAGINAS_ESCANEADAS = 3   # IA local: páginas desenhadas como imagem (mais páginas = mais lento)


class AIError(Exception):
    pass


# ---------------------------------------------------------------- chave da API

def get_api_key() -> str | None:
    try:
        return keyring.get_password(APP_NAME, KEYRING_USER)
    except Exception:
        return None


def set_api_key(key: str) -> None:
    # Fica guardada no Gerenciador de Credenciais do Windows, não em arquivo de texto.
    keyring.set_password(APP_NAME, KEYRING_USER, key.strip())


def _client() -> anthropic.Anthropic:
    key = get_api_key()
    # Sem chave salva, o SDK tenta ANTHROPIC_API_KEY / perfil do `ant auth login`.
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


# ---------------------------------------------------------------- arquivo -> bloco de conteúdo

def _texto_do_pdf(reader: PdfReader, max_paginas: int) -> str | None:
    """Texto das primeiras páginas, ou None se o PDF não tem texto suficiente (escaneado).

    PDFs exportados de sistemas já trazem o texto; assim dispensamos a visão, que é mais lenta e cara.
    """
    paginas = []
    for page in reader.pages[:max_paginas]:
        try:
            bruto = page.extract_text(extraction_mode="layout")
            # O modo layout enche de espaços e linhas em branco: compacta para gastar menos tokens.
            linhas = (re.sub(r"[ \t]{3,}", "  ", ln).strip() for ln in bruto.splitlines())
            paginas.append("\n".join(ln for ln in linhas if ln))
        except Exception:
            paginas.append("")
    texto = "\n\n".join(f"--- Página {i} ---\n{p}" for i, p in enumerate(paginas, 1) if p)
    # Poucos caracteres por página indicam PDF escaneado (imagem): cai para a leitura por visão.
    if sum(len(p) for p in paginas) < MIN_CHARS_POR_PAGINA * max(len(paginas), 1):
        return None
    return texto[:MAX_CHARS_TEXTO_PDF]


def _file_block(path: Path) -> dict:
    ext = path.suffix.lower()
    data = path.read_bytes()

    if ext == ".pdf":
        max_paginas = load_settings()["max_paginas_pdf"]
        try:
            reader = PdfReader(io.BytesIO(data))
            texto = _texto_do_pdf(reader, max_paginas)
            if texto:
                return {"type": "text", "text": f"Conteúdo do PDF (texto extraído):\n\n{texto}"}
            if len(reader.pages) > max_paginas:
                writer = PdfWriter()
                for page in reader.pages[:max_paginas]:
                    writer.add_page(page)
                buf = io.BytesIO()
                writer.write(buf)
                data = buf.getvalue()
        except Exception as e:  # PDF estranho: manda inteiro e deixa a API decidir
            log.warning("Não consegui ler as páginas de %s: %s", path.name, e)
        return {"type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.standard_b64encode(data).decode()}}

    # Imagem: reduz se for grande demais para a API.
    img = Image.open(io.BytesIO(data))
    media = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}.get(img.format)
    if media is None or len(data) > MAX_IMAGEM_BYTES or max(img.size) > MAX_LADO_IMAGEM:
        img = img.convert("RGB")
        img.thumbnail((MAX_LADO_IMAGEM, MAX_LADO_IMAGEM))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        data, media = buf.getvalue(), "image/jpeg"
    return {"type": "image",
            "source": {"type": "base64", "media_type": media, "data": base64.standard_b64encode(data).decode()}}


def _call(system: str, content: list[dict], schema: dict) -> dict:
    """Envia ao motor escolhido nas configurações: IA local (padrão, grátis) ou Claude (pago)."""
    if load_settings()["motor"] == "claude":
        return _call_claude(system, content, schema)
    return _call_local(system, content, schema)


# ---------------------------------------------------------------- IA local (Ollama)

def _paginas_como_imagens(pdf_b64: str, max_paginas: int) -> list[str]:
    """Desenha as primeiras páginas de um PDF escaneado como imagens (PNG em base64)."""
    import fitz  # PyMuPDF
    doc = fitz.open(stream=base64.b64decode(pdf_b64), filetype="pdf")
    imagens = []
    for page in list(doc)[:max_paginas]:
        png = page.get_pixmap(dpi=130).tobytes("png")
        imagens.append(base64.standard_b64encode(png).decode())
    return imagens


def _para_ollama(content: list[dict]) -> tuple[str, list[str]]:
    """Converte os blocos no formato do Claude em (texto, imagens) para o Ollama."""
    textos, imagens = [], []
    for bloco in content:
        if bloco["type"] == "text":
            textos.append(bloco["text"])
        elif bloco["type"] == "image":
            imagens.append(bloco["source"]["data"])
        elif bloco["type"] == "document":
            imagens += _paginas_como_imagens(bloco["source"]["data"], MAX_PAGINAS_ESCANEADAS)
    return "\n\n".join(textos), imagens


def _call_local(system: str, content: list[dict], schema: dict) -> dict:
    settings = load_settings()
    texto, imagens = _para_ollama(content)
    mensagem = {"role": "user", "content": texto}
    if imagens:
        mensagem["images"] = imagens
    corpo = json.dumps({
        "model": settings["modelo_local"],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": settings["contexto_local"]},
        "messages": [{"role": "system", "content": system}, mensagem],
    }).encode()
    pedido = urllib.request.Request(settings["url_ollama"].rstrip("/") + "/api/chat", data=corpo,
                                    headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=900) as resp:
            resposta = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode(errors="replace")[:300]
        if e.code == 404:
            raise AIError(f"Modelo '{settings['modelo_local']}' não instalado no Ollama "
                          f"(rode: ollama pull {settings['modelo_local']}).") from e
        raise AIError(f"Erro do Ollama ({e.code}): {detalhe}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise AIError("Não consegui falar com o Ollama. Ele está aberto no computador?") from e

    texto_resposta = (resposta.get("message") or {}).get("content", "").strip()
    if not texto_resposta:
        raise AIError("A IA local não devolveu resposta.")
    try:
        return json.loads(texto_resposta)
    except json.JSONDecodeError as e:
        raise AIError("A IA local devolveu uma resposta fora do formato esperado.") from e


# ---------------------------------------------------------------- Claude (API paga)

def _call_claude(system: str, content: list[dict], schema: dict) -> dict:
    settings = load_settings()
    modelo = settings["modelo"]
    extra = {}
    if modelo in MODELOS_COM_FALLBACK:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        response = _client().beta.messages.create(
            model=modelo,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            **extra,
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Chave da API inválida. Configure em Configurações.") from e
    except anthropic.RateLimitError as e:
        raise AIError("Limite de uso da API atingido; tentarei de novo mais tarde.") from e
    except anthropic.BadRequestError as e:
        raise AIError(f"A API recusou o arquivo: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Erro da API ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Sem conexão com a internet.") from e

    if response.stop_reason == "refusal":
        raise AIError("A IA se recusou a analisar este arquivo.")
    if response.stop_reason == "max_tokens":
        raise AIError("Resposta da IA incompleta (limite de tokens).")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise AIError("A IA não devolveu resposta.")
    return json.loads(text)


# ---------------------------------------------------------------- identificar documento

def _nullable(tipo: str) -> dict:
    return {"anyOf": [{"type": tipo}, {"type": "null"}]}


def classify(path: Path, types: list[dict]) -> dict:
    """Retorna {tipo_id, confianca, campos: {chave: valor}, resumo}."""
    catalogo = [
        {"id": t["id"], "nome": t["nome"], "descricao": t["descricao"],
         "instrucoes": t.get("instrucoes", ""),
         "campos": [{"chave": c["chave"], "descricao": c["descricao"], "tipo": c.get("tipo", "texto")}
                    for c in t["campos"]]}
        for t in types
    ]
    system = (
        "Você organiza documentos que chegam na pasta Downloads de um usuário no Brasil. "
        "Identifique qual dos tipos cadastrados abaixo corresponde ao arquivo e extraia os campos daquele tipo.\n\n"
        "Regras:\n"
        "- Se o arquivo não corresponder claramente a nenhum tipo, use tipo_id \"nenhum\" e não extraia campos.\n"
        "- Datas sempre no formato AAAA-MM-DD; campos do tipo 'mes' no formato AAAA-MM. Datas brasileiras vêm como DD/MM/AAAA.\n"
        "- Transcreva exatamente o que está escrito no documento, mesmo que pareça errado; "
        "a validação é feita depois pelo programa.\n"
        "- Se um campo não existir ou estiver ilegível, use null.\n"
        "- Nomes de pessoas: escreva com iniciais maiúsculas (ex.: 'Maria da Silva').\n"
        "- resumo: uma frase curta descrevendo o arquivo (sempre preencha, mesmo para 'nenhum').\n\n"
        f"Tipos cadastrados:\n{json.dumps(catalogo, ensure_ascii=False, indent=1)}"
    )
    schema = {
        "type": "object",
        "properties": {
            "tipo_id": {"type": "string", "enum": [t["id"] for t in types] + ["nenhum"]},
            "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
            "campos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"chave": {"type": "string"}, "valor": _nullable("string")},
                    "required": ["chave", "valor"],
                    "additionalProperties": False,
                },
            },
            "resumo": {"type": "string"},
        },
        "required": ["tipo_id", "confianca", "campos", "resumo"],
        "additionalProperties": False,
    }
    content = [_file_block(path),
               {"type": "text", "text": f"Nome do arquivo: {path.name}\nData de hoje: {date.today().isoformat()}"}]
    result = _call(system, content, schema)
    result["campos"] = {c["chave"]: c["valor"] for c in result["campos"]}
    return result


# ---------------------------------------------------------------- aprender tipo a partir de exemplo

def learn_type(path: Path, dica: str = "") -> dict:
    """Analisa um documento de exemplo e propõe a configuração de um novo tipo."""
    system = (
        "Você ajuda a configurar um organizador automático de documentos. O usuário enviou um documento "
        "de EXEMPLO de um tipo que se repete (ex.: holerite, nota fiscal, contrato). Proponha a configuração "
        "desse tipo para que documentos futuros do mesmo tipo sejam reconhecidos e renomeados.\n\n"
        "- nome: nome do tipo em português (ex.: 'Holerite').\n"
        "- descricao: como reconhecer esse tipo de documento, de forma genérica (não cite dados deste exemplo "
        "específico, como nomes de pessoas ou valores).\n"
        "- instrucoes: dicas para achar os campos no documento (onde ficam, como aparecem).\n"
        "- campos: poucos campos úteis para nomear e organizar (normalmente 2 a 4). chave em minúsculas, sem "
        "acento, com _ (ex.: nome, data_emissao). tipo: texto, data, mes ou numero.\n"
        "- padrao_nome: modelo do nome do arquivo usando {chave} dos campos, ex.: '{nome} - {data_emissao}'. "
        "Não inclua a extensão.\n"
        "- pasta_destino: nome curto de pasta no plural (ex.: 'Holerites').\n"
        "- periodo: se o documento cobre um período (data inicial e final), indique os campos e, se o usuário "
        "disser em que dia o período começa/termina, preencha dia_inicio e dia_fim; caso contrário null.\n"
        "- campos_data_nao_futura: campos de data que nunca deveriam estar no futuro.\n"
        "Leve em conta as orientações do usuário, se houver."
    )
    schema = {
        "type": "object",
        "properties": {
            "nome": {"type": "string"},
            "descricao": {"type": "string"},
            "instrucoes": {"type": "string"},
            "campos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "chave": {"type": "string"},
                        "descricao": {"type": "string"},
                        "tipo": {"type": "string", "enum": ["texto", "data", "mes", "numero"]},
                    },
                    "required": ["chave", "descricao", "tipo"],
                    "additionalProperties": False,
                },
            },
            "padrao_nome": {"type": "string"},
            "pasta_destino": {"type": "string"},
            "periodo": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "campo_inicio": {"type": "string"},
                            "campo_fim": {"type": "string"},
                            "dia_inicio": _nullable("integer"),
                            "dia_fim": _nullable("integer"),
                        },
                        "required": ["campo_inicio", "campo_fim", "dia_inicio", "dia_fim"],
                        "additionalProperties": False,
                    },
                ]
            },
            "campos_data_nao_futura": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["nome", "descricao", "instrucoes", "campos", "padrao_nome", "pasta_destino",
                     "periodo", "campos_data_nao_futura"],
        "additionalProperties": False,
    }
    texto = f"Nome do arquivo de exemplo: {path.name}"
    if dica.strip():
        texto += f"\n\nOrientações do usuário:\n{dica.strip()}"
    return _call(system, [_file_block(path), {"type": "text", "text": texto}], schema)
