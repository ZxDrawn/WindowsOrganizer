# WindowsOrganizer

Organiza a pasta Downloads em segundo plano: identifica documentos com IA (Claude), renomeia,
move para a pasta certa, valida datas e registra tudo num histórico local.

## Instalar e rodar
    python -m pip install -r requirements.txt
    pythonw main.py

Na primeira abertura: aba **Configurações** → cole a chave da API Anthropic → Salvar.

## Onde ficam os dados
`%LOCALAPPDATA%\WindowsOrganizer\`
- `historico.db` — histórico (SQLite)
- `tipos.json` — tipos de documento
- `config.json` — configurações
- `app.log` — log técnico

## Estrutura
- `organizer/engine.py` — vigia a pasta, processa, move, detecta exclusões
- `organizer/ai.py` — chamadas ao Claude (identificar e aprender tipos)
- `organizer/rules.py` — regras de validação (período de referência, data no futuro)
- `organizer/gui.py` — janela (histórico, tipos, configurações)
- `main.py` — ícone na bandeja e inicialização
