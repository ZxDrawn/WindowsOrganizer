"""Janela principal: histórico, tipos de documento e configurações."""
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import unicodedata
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import ai, db
from .config import APP_NAME, load_settings, load_types, save_settings, save_types
from .engine import Organizer

EVENTOS = ["", "organizado", "revisar", "nao_reconhecido", "novo", "apagado", "movido", "renomeado", "erro",
           "inventario"]
MODELOS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
TIPOS_CAMPO = ("texto", "data", "mes", "numero")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_") or "tipo"


def abrir_no_explorer(caminho: str) -> None:
    if caminho and os.path.exists(caminho):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(caminho)])
    elif caminho and os.path.isdir(os.path.dirname(caminho)):
        os.startfile(os.path.dirname(caminho))


class App:
    def __init__(self, root: tk.Tk, organizer: Organizer):
        self.root = root
        self.org = organizer
        root.title(APP_NAME)
        root.geometry("1050x640")
        root.minsize(820, 500)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)  # fechar = esconder; continua na bandeja

        abas = ttk.Notebook(root)
        abas.pack(fill="both", expand=True, padx=8, pady=8)
        self._aba_historico(abas)
        self._aba_tipos(abas)
        self._aba_config(abas)
        self._atualizar_historico()

    def mostrar(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._atualizar_historico()

    # ================================================================ HISTÓRICO

    def _aba_historico(self, abas: ttk.Notebook) -> None:
        f = ttk.Frame(abas, padding=8)
        abas.add(f, text="Histórico")

        barra = ttk.Frame(f)
        barra.pack(fill="x")
        ttk.Label(barra, text="Buscar:").pack(side="left")
        self.busca = tk.StringVar()
        e = ttk.Entry(barra, textvariable=self.busca, width=40)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _: self._atualizar_historico())
        ttk.Label(barra, text="Evento:").pack(side="left", padx=(12, 0))
        self.filtro = tk.StringVar()
        cb = ttk.Combobox(barra, textvariable=self.filtro, values=EVENTOS, width=16, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _: self._atualizar_historico())
        ttk.Button(barra, text="Atualizar", command=self._atualizar_historico).pack(side="left", padx=4)

        colunas = ("quando", "evento", "arquivo", "tipo", "detalhe")
        self.tree = ttk.Treeview(f, columns=colunas, show="headings")
        for col, titulo, larg in [("quando", "Quando", 140), ("evento", "Evento", 110), ("arquivo", "Arquivo", 260),
                                  ("tipo", "Tipo", 130), ("detalhe", "Destino / observação", 380)]:
            self.tree.heading(col, text=titulo)
            self.tree.column(col, width=larg, anchor="w")
        self.tree.tag_configure("problema", foreground="#b00020")
        self.tree.tag_configure("apagado", foreground="#777777")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, pady=(8, 0))
        sb.pack(side="right", fill="y", pady=(8, 0))
        self.tree.bind("<Double-1>", self._detalhe_evento)
        self._linhas: dict[str, dict] = {}

    def _atualizar_historico(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._linhas.clear()
        for r in db.search_events(self.busca.get().strip(), self.filtro.get()):
            r = dict(r)
            detalhe = " | ".join(x for x in [
                Path(r["destino"]).relative_to(self.org.root).as_posix() if r["destino"] and _dentro(r["destino"], self.org.root) else r["destino"],
                r["observacao"], None if r["destino"] else r["resumo"]] if x)
            tags = ("problema",) if r["observacao"] and r["evento"] in ("organizado", "revisar", "erro") else \
                ("apagado",) if r["evento"] == "apagado" else ()
            iid = self.tree.insert("", "end", values=(r["quando"].replace("T", " "), r["evento"], r["arquivo"],
                                                      r["tipo"] or "", detalhe), tags=tags)
            self._linhas[iid] = r

    def _detalhe_evento(self, _event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        r = self._linhas[sel[0]]
        win = tk.Toplevel(self.root)
        win.title(r["arquivo"])
        win.geometry("640x420")
        txt = tk.Text(win, wrap="word", padx=8, pady=8)
        txt.pack(fill="both", expand=True)
        campos = json.loads(r["campos"]) if r["campos"] else {}
        linhas = [f"Quando: {r['quando'].replace('T', ' ')}", f"Evento: {r['evento']}",
                  f"Arquivo original: {r['arquivo']}", f"Origem: {r['caminho'] or ''}",
                  f"Destino: {r['destino'] or ''}", f"Tipo: {r['tipo'] or ''}", "",
                  f"Resumo: {r['resumo'] or ''}", f"Observação: {r['observacao'] or ''}", "", "Dados extraídos:"]
        linhas += [f"  {k}: {v}" for k, v in campos.items()]
        txt.insert("1.0", "\n".join(linhas))
        txt.configure(state="disabled")
        alvo = r["destino"] or r["caminho"]
        ttk.Button(win, text="Mostrar no Explorer", command=lambda: abrir_no_explorer(alvo)).pack(pady=6)

    # ================================================================ TIPOS DE DOCUMENTO

    def _aba_tipos(self, abas: ttk.Notebook) -> None:
        f = ttk.Frame(abas, padding=8)
        abas.add(f, text="Tipos de documento")

        esq = ttk.Frame(f)
        esq.pack(side="left", fill="y")
        self.lista_tipos = tk.Listbox(esq, width=28, exportselection=False)
        self.lista_tipos.pack(fill="y", expand=True)
        self.lista_tipos.bind("<<ListboxSelect>>", self._selecionar_tipo)
        ttk.Button(esq, text="Aprender com exemplo…", command=self._aprender).pack(fill="x", pady=(6, 2))
        ttk.Button(esq, text="Novo (em branco)", command=self._novo_tipo).pack(fill="x", pady=2)
        ttk.Button(esq, text="Excluir", command=self._excluir_tipo).pack(fill="x", pady=2)

        form = ttk.Frame(f, padding=(12, 0, 0, 0))
        form.pack(side="left", fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        self.v = {k: tk.StringVar() for k in ("nome", "padrao_nome", "pasta_destino", "p_inicio", "p_fim",
                                              "p_dia_ini", "p_dia_fim", "p_sufixo", "nao_futuras", "nf_sufixo")}
        self.v_periodo = tk.BooleanVar()

        def linha(r, rotulo, widget, dica=""):
            ttk.Label(form, text=rotulo).grid(row=r, column=0, sticky="nw", pady=3)
            widget.grid(row=r, column=1, sticky="ew", pady=3)
            if dica:
                ttk.Label(form, text=dica, foreground="#666").grid(row=r + 1, column=1, sticky="w")

        linha(0, "Nome:", ttk.Entry(form, textvariable=self.v["nome"]))
        self.t_desc = tk.Text(form, height=3, wrap="word")
        linha(1, "Como reconhecer:", self.t_desc)
        self.t_instr = tk.Text(form, height=2, wrap="word")
        linha(2, "Instruções à IA:", self.t_instr)
        self.t_campos = tk.Text(form, height=5, wrap="none")
        linha(3, "Campos:", self.t_campos, "Um por linha:  chave | tipo (texto, data, mes, numero) | descrição")
        linha(5, "Nome do arquivo:", ttk.Entry(form, textvariable=self.v["padrao_nome"]),
              "Use {chave}. Ex.: {nome} - {mes_referencia}   ·   datas: {data:%d-%m-%Y}")
        linha(7, "Pasta de destino:", ttk.Entry(form, textvariable=self.v["pasta_destino"]),
              "Subpasta dentro da pasta monitorada")

        reg = ttk.LabelFrame(form, text="Regra: período de referência", padding=6)
        reg.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Checkbutton(reg, text="Documento cobre um período", variable=self.v_periodo).grid(row=0, column=0, columnspan=6, sticky="w")
        for i, (rot, k, w) in enumerate([("Campo início", "p_inicio", 14), ("Campo fim", "p_fim", 14),
                                          ("Começa no dia", "p_dia_ini", 4), ("Termina no dia", "p_dia_fim", 4)]):
            ttk.Label(reg, text=rot).grid(row=1, column=i * 2, sticky="w", padx=(0 if i == 0 else 10, 2))
            ttk.Entry(reg, textvariable=self.v[k], width=w).grid(row=1, column=i * 2 + 1, sticky="w")
        ttk.Label(reg, text="Texto no nome se errado").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(reg, textvariable=self.v["p_sufixo"], width=30).grid(row=2, column=1, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Label(reg, text="Se começa num dia maior que o do fim (ex.: 19 → 18), o início é no mês anterior. "
                            "Gera o campo {mes_referencia}.", foreground="#666").grid(row=3, column=0, columnspan=8, sticky="w")

        reg2 = ttk.LabelFrame(form, text="Regra: datas que não podem estar no futuro", padding=6)
        reg2.grid(row=10, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(reg2, text="Campos (separados por vírgula)").grid(row=0, column=0, sticky="w")
        ttk.Entry(reg2, textvariable=self.v["nao_futuras"], width=30).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(reg2, text="Texto no nome").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Entry(reg2, textvariable=self.v["nf_sufixo"], width=22).grid(row=0, column=3, sticky="w", padx=4)

        ttk.Button(form, text="Salvar tipo", command=self._salvar_tipo).grid(row=11, column=1, sticky="e", pady=10)
        self.status_tipo = ttk.Label(form, text="", foreground="#0a5")
        self.status_tipo.grid(row=11, column=0, sticky="w")

        self.tipos = load_types()
        self.tipo_atual: dict | None = None
        self._recarregar_lista()

    def _recarregar_lista(self, selecionar_id: str | None = None) -> None:
        self.lista_tipos.delete(0, "end")
        for t in self.tipos:
            self.lista_tipos.insert("end", t["nome"])
        idx = next((i for i, t in enumerate(self.tipos) if t["id"] == selecionar_id), 0 if self.tipos else None)
        if idx is not None:
            self.lista_tipos.selection_set(idx)
            self._preencher(self.tipos[idx])

    def _selecionar_tipo(self, _event=None) -> None:
        sel = self.lista_tipos.curselection()
        if sel:
            self._preencher(self.tipos[sel[0]])

    def _preencher(self, t: dict) -> None:
        self.tipo_atual = t
        self.v["nome"].set(t.get("nome", ""))
        self.v["padrao_nome"].set(t.get("padrao_nome", ""))
        self.v["pasta_destino"].set(t.get("pasta_destino", ""))
        for widget, chave in ((self.t_desc, "descricao"), (self.t_instr, "instrucoes")):
            widget.delete("1.0", "end")
            widget.insert("1.0", t.get(chave, ""))
        self.t_campos.delete("1.0", "end")
        self.t_campos.insert("1.0", "\n".join(f"{c['chave']} | {c.get('tipo', 'texto')} | {c['descricao']}"
                                              for c in t.get("campos", [])))
        per = next((r for r in t.get("regras", []) if r["tipo"] == "periodo_referencia"), None)
        self.v_periodo.set(per is not None)
        per = per or {}
        self.v["p_inicio"].set(per.get("campo_inicio", ""))
        self.v["p_fim"].set(per.get("campo_fim", ""))
        self.v["p_dia_ini"].set(per.get("dia_inicio") or "")
        self.v["p_dia_fim"].set(per.get("dia_fim") or "")
        self.v["p_sufixo"].set(per.get("sufixo_erro", "DATA ERRADA"))
        nf = [r for r in t.get("regras", []) if r["tipo"] == "data_nao_futura"]
        self.v["nao_futuras"].set(", ".join(r["campo"] for r in nf))
        self.v["nf_sufixo"].set(nf[0].get("sufixo_erro", "DATA NO FUTURO") if nf else "DATA NO FUTURO")
        self.status_tipo.configure(text="")

    def _novo_tipo(self) -> None:
        self.lista_tipos.selection_clear(0, "end")
        self._preencher({"campos": [{"chave": "nome", "tipo": "texto", "descricao": "Nome da pessoa"},
                                    {"chave": "data", "tipo": "data", "descricao": "Data do documento"}],
                         "padrao_nome": "{nome} - {data}"})
        self.tipo_atual = None

    def _ler_form(self) -> dict:
        nome = self.v["nome"].get().strip()
        if not nome:
            raise ValueError("Dê um nome ao tipo de documento.")
        campos = []
        for n, linha in enumerate(self.t_campos.get("1.0", "end").strip().splitlines(), 1):
            if not linha.strip():
                continue
            partes = [p.strip() for p in linha.split("|")]
            if len(partes) < 3 or partes[1] not in TIPOS_CAMPO:
                raise ValueError(f"Linha {n} dos campos inválida. Formato: chave | tipo | descrição\n"
                                 f"tipos: {', '.join(TIPOS_CAMPO)}")
            campos.append({"chave": slug(partes[0]), "tipo": partes[1], "descricao": " | ".join(partes[2:])})
        if not campos:
            raise ValueError("Inclua ao menos um campo.")
        regras = []
        if self.v_periodo.get():
            chaves = {c["chave"] for c in campos}
            ini, fim = slug(self.v["p_inicio"].get()), slug(self.v["p_fim"].get())
            if ini not in chaves or fim not in chaves:
                raise ValueError("Os campos de início e fim do período precisam existir na lista de campos.")
            dias = []
            for k in ("p_dia_ini", "p_dia_fim"):
                d = self.v[k].get().strip()
                if d and not (d.isdigit() and 1 <= int(d) <= 31):
                    raise ValueError("Dias do período devem ser números de 1 a 31.")
                dias.append(int(d) if d else None)
            regras.append({"tipo": "periodo_referencia", "campo_inicio": ini, "campo_fim": fim,
                           "dia_inicio": dias[0], "dia_fim": dias[1],
                           "sufixo_erro": self.v["p_sufixo"].get().strip() or "DATA ERRADA"})
        for campo in filter(None, (slug(c) for c in self.v["nao_futuras"].get().split(",") if c.strip())):
            regras.append({"tipo": "data_nao_futura", "campo": campo,
                           "sufixo_erro": self.v["nf_sufixo"].get().strip() or "DATA NO FUTURO"})
        return {
            "id": self.tipo_atual["id"] if self.tipo_atual and self.tipo_atual.get("id") else slug(nome),
            "nome": nome,
            "descricao": self.t_desc.get("1.0", "end").strip(),
            "instrucoes": self.t_instr.get("1.0", "end").strip(),
            "campos": campos,
            "padrao_nome": self.v["padrao_nome"].get().strip() or "{" + campos[0]["chave"] + "}",
            "pasta_destino": self.v["pasta_destino"].get().strip() or nome,
            "regras": regras,
        }

    def _salvar_tipo(self) -> None:
        try:
            t = self._ler_form()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return
        existente = next((i for i, x in enumerate(self.tipos) if x["id"] == t["id"]), None)
        if existente is None:
            # id novo: evita colidir com outro tipo de mesmo nome
            base, n = t["id"], 2
            while any(x["id"] == t["id"] for x in self.tipos):
                t["id"] = f"{base}_{n}"
                n += 1
            self.tipos.append(t)
        else:
            self.tipos[existente] = t
        save_types(self.tipos)
        self._recarregar_lista(t["id"])
        self.status_tipo.configure(text="Salvo ✔")

    def _excluir_tipo(self) -> None:
        sel = self.lista_tipos.curselection()
        if not sel:
            return
        t = self.tipos[sel[0]]
        if messagebox.askyesno(APP_NAME, f"Excluir o tipo \"{t['nome']}\"?\n(Os arquivos já organizados não são afetados.)",
                               parent=self.root):
            del self.tipos[sel[0]]
            save_types(self.tipos)
            self._recarregar_lista()

    def _aprender(self) -> None:
        caminho = filedialog.askopenfilename(
            parent=self.root, title="Escolha um documento de exemplo",
            filetypes=[("Documentos", "*.pdf *.jpg *.jpeg *.png *.webp"), ("Todos", "*.*")])
        if not caminho:
            return
        dica = self._pedir_dica()
        if dica is None:
            return
        self.status_tipo.configure(text="Analisando o exemplo com a IA…", foreground="#06c")

        def trabalho():
            try:
                proposta = ai.learn_type(Path(caminho), dica)
                self.root.after(0, lambda: self._aplicar_proposta(proposta))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda: (self.status_tipo.configure(text=""),
                                            messagebox.showerror(APP_NAME, f"Não consegui analisar o exemplo:\n{msg}",
                                                                 parent=self.root)))
        threading.Thread(target=trabalho, daemon=True).start()

    def _pedir_dica(self) -> str | None:
        win = tk.Toplevel(self.root)
        win.title("Orientações (opcional)")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, padding=8, wraplength=460,
                  text="Quer explicar algo sobre esse documento? Ex.: \"o período começa no dia 19 do mês anterior "
                       "e termina no dia 18\", \"quero o nome com o CNPJ do fornecedor\". Pode deixar em branco.").pack()
        txt = tk.Text(win, height=5, width=60, wrap="word")
        txt.pack(padx=8)
        resultado: dict = {}

        def ok():
            resultado["dica"] = txt.get("1.0", "end").strip()
            win.destroy()
        botoes = ttk.Frame(win, padding=8)
        botoes.pack(fill="x")
        ttk.Button(botoes, text="Analisar", command=ok).pack(side="right")
        ttk.Button(botoes, text="Cancelar", command=win.destroy).pack(side="right", padx=6)
        txt.focus_set()
        self.root.wait_window(win)
        return resultado.get("dica")

    def _aplicar_proposta(self, p: dict) -> None:
        regras = []
        per = p.get("periodo")
        if per:
            regras.append({"tipo": "periodo_referencia", "campo_inicio": per["campo_inicio"],
                           "campo_fim": per["campo_fim"], "dia_inicio": per.get("dia_inicio"),
                           "dia_fim": per.get("dia_fim"), "sufixo_erro": "DATA ERRADA"})
        for c in p.get("campos_data_nao_futura", []):
            regras.append({"tipo": "data_nao_futura", "campo": c, "sufixo_erro": "DATA NO FUTURO"})
        self.lista_tipos.selection_clear(0, "end")
        self._preencher({**p, "regras": regras})
        self.tipo_atual = None
        self.status_tipo.configure(text="Proposta da IA carregada. Revise e clique em Salvar.", foreground="#06c")

    # ================================================================ CONFIGURAÇÕES

    def _aba_config(self, abas: ttk.Notebook) -> None:
        f = ttk.Frame(abas, padding=12)
        abas.add(f, text="Configurações")
        f.columnconfigure(1, weight=1)
        s = load_settings()

        self.c_chave = tk.StringVar()
        self.c_pasta = tk.StringVar(value=s["pasta_monitorada"])
        self.c_modelo = tk.StringVar(value=s["modelo"])
        self.c_varredura = tk.StringVar(value=str(s["varredura_minutos"]))
        self.c_iniciar = tk.BooleanVar(value=self._inicia_com_windows())

        ttk.Label(f, text="Chave da API Anthropic:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.c_chave, show="•").grid(row=0, column=1, sticky="ew", pady=4)
        self.lbl_chave = ttk.Label(f, text="✔ chave salva" if ai.get_api_key() else "nenhuma chave salva",
                                   foreground="#0a5" if ai.get_api_key() else "#b00020")
        self.lbl_chave.grid(row=0, column=2, sticky="w", padx=6)
        ttk.Label(f, text="Guardada no Gerenciador de Credenciais do Windows. Deixe em branco para manter a atual.",
                  foreground="#666").grid(row=1, column=1, sticky="w")

        ttk.Label(f, text="Pasta monitorada:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.c_pasta).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="Escolher…", command=lambda: self.c_pasta.set(
            filedialog.askdirectory(parent=self.root, initialdir=self.c_pasta.get()) or self.c_pasta.get())
                   ).grid(row=2, column=2, padx=6)

        ttk.Label(f, text="Modelo de IA:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.c_modelo, values=MODELOS).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(f, text="Opus 5: mais preciso · Sonnet 5: mais barato · Haiku 4.5: o mais barato",
                  foreground="#666").grid(row=4, column=1, sticky="w")

        ttk.Label(f, text="Varredura a cada (min):").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.c_varredura, width=6).grid(row=5, column=1, sticky="w", pady=4)

        ttk.Checkbutton(f, text="Iniciar junto com o Windows", variable=self.c_iniciar).grid(
            row=6, column=1, sticky="w", pady=8)

        ttk.Button(f, text="Salvar configurações", command=self._salvar_config).grid(row=7, column=1, sticky="w")

        ttk.Separator(f).grid(row=8, column=0, columnspan=3, sticky="ew", pady=16)
        ttk.Label(f, text="Na primeira execução os arquivos que já estavam na pasta são apenas registrados, "
                          "não movidos. Para organizá-los também:", wraplength=700).grid(row=9, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Organizar agora os documentos soltos na pasta",
                   command=self._organizar_existentes).grid(row=10, column=0, columnspan=2, sticky="w", pady=6)

    def _salvar_config(self) -> None:
        s = load_settings()
        if not self.c_varredura.get().isdigit() or int(self.c_varredura.get()) < 1:
            messagebox.showerror(APP_NAME, "Varredura deve ser um número de minutos (1 ou mais).", parent=self.root)
            return
        if not os.path.isdir(self.c_pasta.get()):
            messagebox.showerror(APP_NAME, "A pasta monitorada não existe.", parent=self.root)
            return
        if self.c_chave.get().strip():
            ai.set_api_key(self.c_chave.get())
            self.c_chave.set("")
            self.lbl_chave.configure(text="✔ chave salva", foreground="#0a5")
        pasta_mudou = os.path.normcase(s["pasta_monitorada"]) != os.path.normcase(self.c_pasta.get())
        s.update(pasta_monitorada=self.c_pasta.get(), modelo=self.c_modelo.get().strip(),
                 varredura_minutos=int(self.c_varredura.get()))
        if pasta_mudou:
            s["inventario_feito"] = False  # pasta nova: registra o que já existe sem mexer
        save_settings(s)
        self._definir_inicio_windows(self.c_iniciar.get())
        if pasta_mudou:
            self.org.restart()
        messagebox.showinfo(APP_NAME, "Configurações salvas.", parent=self.root)

    def _organizar_existentes(self) -> None:
        if not (ai.get_api_key() or os.environ.get("ANTHROPIC_API_KEY")):
            messagebox.showerror(APP_NAME, "Configure a chave da API primeiro.", parent=self.root)
            return
        if messagebox.askyesno(APP_NAME, "Todos os PDFs e imagens soltos na pasta serão analisados pela IA e os "
                                         "reconhecidos serão renomeados e movidos. Continuar?", parent=self.root):
            n = self.org.organizar_existentes()
            messagebox.showinfo(APP_NAME, f"{n} arquivo(s) na fila. Acompanhe na aba Histórico.", parent=self.root)

    @staticmethod
    def _comando_inicio() -> str:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        main = Path(__file__).resolve().parent.parent / "main.py"
        return f'"{pythonw}" "{main}" --minimizado'

    def _inicia_com_windows(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
                return True
        except OSError:
            return False

    def _definir_inicio_windows(self, ativo: bool) -> None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if ativo:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, self._comando_inicio())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass


def _dentro(caminho: str, pasta: Path) -> bool:
    try:
        Path(caminho).relative_to(pasta)
        return True
    except ValueError:
        return False
