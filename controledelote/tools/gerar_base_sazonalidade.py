#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador da BASE DE SAZONALIDADE para o Controle de Auditoria de Lote.

O que faz
---------
Lê o export bruto de vendas (arquivo .xlsx grande, com VÁRIAS abas), filtra os
CFOP de venda (conta o MÊS QUE VENDEU, não o que entregou), agrega por
PRODUTO x FILIAL x MÊS e cospe um arquivo PEQUENO (sazonal_base.json) no mesmo
formato compacto que o app usa. O arquivão de 310 MB NUNCA sai da sua máquina.

Como usar (Windows / Mac / Linux)
---------------------------------
1) Instale o Python 3.9+ (https://www.python.org/downloads/  — marque "Add to PATH").
2) Instale as dependências (uma vez só), no Prompt/Terminal:

       py -m pip install pandas openpyxl lxml xlrd

   (lxml e xlrd cobrem exports de ERP em .xls — inclusive HTML disfarçado de .xls)

3) Rode, apontando para o seu arquivo de vendas:

       python gerar_base_sazonalidade.py "C:\\caminho\\vendas_jan24_jun26.xlsx"

   (coloque o caminho entre aspas se tiver espaços)

4) Ele vai IMPRIMIR as colunas que detectou e pedir confirmação. Se o mapeamento
   estiver certo, ele gera "sazonal_base.json" na mesma pasta. Esse arquivo é
   pequeno — é só ele que você me envia (ou faz commit).

Se a detecção automática errar alguma coluna, edite o bloco COLUNAS_MANUAIS lá
embaixo (logo após os imports) com o nome EXATO da coluna no seu arquivo.

Regras de negócio (iguais às do app) — dois eixos: VENDA x MOVIMENTAÇÃO de estoque
----------------------------------------------------------------------------------
A sazonalidade mede o MÊS QUE VENDEU (faturamento), NÃO o mês que entregou.
- CFOP contados como VENDA (entram na sazonalidade): 5101 5102 5120 5405 6101 6102 5922 6922
    * 5101/5102/5120/5405/6101/6102 = venda normal (VENDE e MOVIMENTA estoque no ato).
    * 5922/6922 = faturamento p/ entrega futura: É VENDA, mas NÃO movimenta o estoque
      (a mercadoria sai depois, na remessa). Conta no MÊS DO FATURAMENTO.
- CFOP excluídos = remessa de entrega futura, NÃO é venda: 5116 5117 6116 6117
    * A mercadoria SAI do estoque (MOVIMENTA), mas a venda já foi contada no faturamento
      (5922/6922). Excluídos para não contar "entrega" como "venda" nem duplicar a venda.
- Período mantido: Jan/2024 a Jun/2026 (configurável abaixo).
- Sazonalidade medida em UNIDADES (soma da quantidade), por mês (Jan=0 ... Dez=11).
"""

import sys, os, json, re, argparse
from datetime import datetime, date

# ======================= CONFIGURAÇÃO =======================

# CFOPs (apenas os 4 últimos dígitos importam)
# VENDA (entra na sazonalidade = "mês que vendeu"): venda normal + 5922/6922 (venda que
# NÃO movimenta estoque ainda — faturamento de entrega futura, conta no mês do faturamento).
CFOPS_VENDA   = {"5101", "5102", "5120", "5405", "6101", "6102", "5922", "6922"}
# REMESSA de entrega futura: MOVIMENTA estoque, mas NÃO é venda (a venda já foi no 5922/6922).
# Excluída da sazonalidade para não contar "entrega" como "venda" nem duplicar.
# Mesmo assim, é registrada separadamente (campo "remessa" no JSON de saída): um produto que
# só aparece em remessa (sem 5922/6922 correspondente nos dados, ex. erro de faturamento, ou
# fora do período de venda analisado) MOVIMENTOU estoque e não pode ser tratado como "nunca
# vendeu" (dead stock) sem checar esse CFOP manualmente.
CFOPS_EXCLUIR = {"5116", "5117", "6116", "6117"}

# Janela de histórico mantida (ano, mês) inclusive
PERIODO_INI = (2024, 1)
PERIODO_FIM = (2026, 6)

# LAYOUT POR POSIÇÃO (0-based). Este relatório (relatorioResumoMovimentacaoCfop) tem 51
# colunas fixas e as abas de continuação vêm SEM cabeçalho — então lemos por posição.
# Deixe COLUNAS_POR_POSICAO = None para voltar à detecção automática por nome de cabeçalho.
COLUNAS_POR_POSICAO = {
    "filial": 1,      # Filial (sigla)
    "data": 24,       # Emissão (data da venda/faturamento)
    "produto": 26,    # Cod.Produto
    "nome": 27,       # Produto (nome)
    "cfop": 30,       # CFOP
    "quantidade": 35, # Qtde
}

# Ordem fixa das siglas usada pelo app (NÃO reordenar — os índices casam com a base embutida).
# Siglas novas encontradas no arquivo são acrescentadas ao final automaticamente.
BASE_SIGLAS = ["ALT","ARG","BAR","CAN","CON","CRI","FAG","FAP","FCB","FOR","GRA",
               "GUA","GUR","IMP","JAT","JUS","MAR","MIN","MOR","MOZ","PAL","PGM",
               "PLA","POR","RED","RIA","RVD","SFX","UBR","UNA","URU","VNP","XRA"]

# Mapa: número da filial -> sigla (para o caso de o arquivo trazer a filial por número).
NUM2SIGLA = {
    1:"MTZ", 3:"ARG", 4:"RVD", 5:"UBR", 10:"MAR", 11:"JAT", 12:"CRI", 13:"FAG",
    14:"RED", 16:"IMP", 17:"BAR", 19:"PAL", 21:"MOZ", 22:"PGM", 23:"SFX", 24:"FAP",
    25:"FCB", 27:"URU", 28:"MOR", 29:"CON", 30:"FOR", 31:"JUS", 32:"XRA", 34:"POR",
    35:"PLA", 36:"GRA", 37:"CAN", 38:"GUR", 40:"RIA", 41:"ALT",
}

# Se a detecção automática errar, preencha aqui com o NOME EXATO da coluna no seu
# arquivo (deixe None para detecção automática). Ex.: "COD_PRODUTO": "Cód. Produto"
COLUNAS_MANUAIS = {
    "produto":   None,   # código do produto
    "nome":      None,   # nome do produto (opcional)
    "filial":    None,   # sigla OU número da filial
    "data":      None,   # data de emissão/venda (NÃO a data de entrega)
    "cfop":      None,   # CFOP
    "quantidade":None,   # quantidade vendida
}

# Palavras-chave para detectar cada coluna automaticamente (minúsculas, sem acento).
PISTAS = {
    "produto":    [["cod", "produto"], ["codigo", "produto"], ["cod", "prod"], ["id", "produto"], ["produto", "codigo"]],
    "nome":       [["nome", "produto"], ["descricao", "produto"], ["produto", "nome"], ["descricao"]],
    "filial":     [["filial"], ["loja"], ["sigla"], ["unidade"], ["empresa"]],
    "data":       [["data", "emiss"], ["dt", "emiss"], ["data", "venda"], ["data", "moviment"], ["emissao"], ["data"], ["dt", "venda"]],
    "cfop":       [["cfop"], ["natureza", "oper"], ["nat", "op"]],
    "quantidade": [["quant"], ["qtd"], ["qtde"], ["qte"]],
}

# ======================= UTILIDADES =======================

def strip_acentos(s):
    repl = (("á","a"),("à","a"),("ã","a"),("â","a"),("é","e"),("ê","e"),("í","i"),
            ("ó","o"),("ô","o"),("õ","o"),("ú","u"),("ç","c"))
    s = s.lower()
    for a, b in repl:
        s = s.replace(a, b)
    return s

def achar_coluna(headers, pistas, manual):
    """Retorna o nome do cabeçalho que melhor casa com as pistas, ou None."""
    if manual:
        for h in headers:
            if str(h).strip() == str(manual).strip():
                return h
        print(f"  [aviso] coluna manual '{manual}' não encontrada — tentando detecção automática.")
    norm = {h: strip_acentos(str(h)) for h in headers}
    for grupo in pistas:
        for h, hn in norm.items():
            if all(p in hn for p in grupo):
                return h
    return None

def parse_qtd(v):
    if v is None: return 0.0
    s = str(v).strip()
    if s == "" or s.lower() == "nan": return 0.0
    s = s.replace("R$", "").replace(" ", "")
    # número pt-BR: 1.234,56 -> 1234.56
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def parse_cfop(v):
    """Extrai o código CFOP (4 dígitos) que vem no INÍCIO do campo.
    Aceita '5102', '5102.0', '5.102' e '5102 - 00 - VENDA MERC ADQ'."""
    if v is None: return ""
    s = str(v).strip()
    token = re.split(r"[\s\-–]+", s, maxsplit=1)[0]   # "5102" de "5102 - 00 - VENDA..."
    d = re.sub(r"\D", "", token)
    if len(d) >= 4:
        return d[:4]
    m = re.search(r"\d{4}", re.sub(r"(?<=\d)[.\s](?=\d)", "", s))  # fallback: 1º grupo de 4 dígitos
    return m.group(0) if m else d

def parse_ano_mes(v):
    """Retorna (ano, mes) 1-12, ou None. Aceita datetime, serial Excel e strings pt-BR."""
    if v is None: return None
    if isinstance(v, (datetime, date)):
        return (v.year, v.month)
    s = str(v).strip()
    if s == "" or s.lower() == "nan": return None
    # serial do Excel (ex.: "45292")
    if re.fullmatch(r"\d{5}", s):
        try:
            base = datetime(1899, 12, 30)
            dt = base.fromordinal(base.toordinal() + int(s))
            return (dt.year, dt.month)
        except Exception:
            pass
    # formatos comuns
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y",
                "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s[:len(fmt)+8], fmt)
            return (dt.year, dt.month)
        except ValueError:
            continue
    # só mês/ano: 01/2024 ou 2024-01
    m = re.match(r"(\d{1,2})[/\-](\d{4})$", s)
    if m: return (int(m.group(2)), int(m.group(1)))
    m = re.match(r"(\d{4})[/\-](\d{1,2})$", s)
    if m: return (int(m.group(1)), int(m.group(2)))
    return None

def no_periodo(ano, mes):
    return PERIODO_INI <= (ano, mes) <= PERIODO_FIM

def resolver_sigla(v, siglas_idx):
    """Converte o conteúdo da coluna filial em sigla de 3 letras."""
    if v is None: return None
    s = str(v).strip().upper()
    if s == "" or s == "NAN": return None
    # número da filial
    dnum = re.sub(r"\D", "", s)
    if dnum and dnum == s.replace(".0", ""):
        n = int(dnum)
        if n in NUM2SIGLA: return NUM2SIGLA[n]
    # "28 - MORRINHOS" -> tenta número antes do hífen
    m = re.match(r"\s*(\d+)\s*[-–]", s)
    if m and int(m.group(1)) in NUM2SIGLA:
        return NUM2SIGLA[int(m.group(1))]
    # já é sigla de 3 letras conhecida
    sig = re.sub(r"[^A-Z]", "", s)[:3]
    if sig in siglas_idx or len(sig) == 3:
        return sig
    return sig or None

# ======================= LEITURA (xlsx / xls binário / HTML disfarçado) =======================

def _ler_raw(path):
    """Carrega o arquivo (qualquer formato) como dict {nome_aba: DataFrame SEM cabeçalho, tudo str}."""
    import pandas as pd
    with open(path, "rb") as f:
        head = f.read(4096)
    low = head.lower()
    ext = os.path.splitext(path)[1].lower()
    eh_html = head[:1] == b"<" or b"<table" in low or b"<html" in low or b"<tr" in low or (b"<?xml" in low and b"<table" in low)

    def excel(engine, pacote):
        try:
            d = pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine=engine)
            return {k: v.astype(str) for k, v in d.items()}
        except ImportError:
            print(f"ERRO: falta o pacote '{pacote}' para ler este arquivo. Rode:\n    py -m pip install {pacote}")
            sys.exit(1)

    def html():
        try:
            tabelas = pd.read_html(path, header=None)
        except ImportError:
            print("ERRO: este arquivo é um HTML disfarçado de .xls. Instale o leitor:\n    py -m pip install lxml")
            sys.exit(1)
        return {f"Tabela {i+1}": t.astype(str) for i, t in enumerate(tabelas)}

    if head[:2] == b"PK":                       # zip -> xlsx
        return excel("openpyxl", "openpyxl")
    if head[:4] == b"\xD0\xCF\x11\xE0":         # OLE2 -> xls binário
        return excel("xlrd", "xlrd")
    if eh_html:                                 # HTML/XML disfarçado de .xls
        return html()
    # extensão sugere excel mas conteúdo não bateu -> tenta excel e cai para html
    if ext in (".xlsx", ".xls"):
        try:
            return excel(None, "openpyxl")
        except SystemExit:
            raise
        except Exception:
            return html()
    return html()

_ALVOS_HEADER = ["cfop", "produto", "quant", "qtd", "filial", "loja", "data", "emiss", "cod"]

def _score_keys(valores):
    return sum(1 for x in valores if any(a in strip_acentos(str(x)) for a in _ALVOS_HEADER))

def _linha_header(df_raw):
    """Acha a linha que é o cabeçalho (a com mais palavras-chave de coluna)."""
    melhor_i, melhor_score = 0, -1
    for i in range(min(25, len(df_raw))):
        score = _score_keys(df_raw.iloc[i].tolist())
        if score > melhor_score:
            melhor_score, melhor_i = score, i
    return melhor_i, melhor_score

def carregar_abas(path):
    """Retorna dict {nome_aba: DataFrame com cabeçalho correto}.
    Se as colunas já forem o cabeçalho (ex.: HTML com <th>), mantém; senão promove a melhor linha."""
    raw = _ler_raw(path)
    out = {}
    for nome, dfr in raw.items():
        if dfr is None or len(dfr) == 0:
            out[nome] = dfr
            continue
        col_score = _score_keys(list(dfr.columns))
        i, row_score = _linha_header(dfr)
        if col_score >= 2 and col_score >= row_score:
            out[nome] = dfr.reset_index(drop=True)          # cabeçalho já está nas colunas
        else:
            header = [str(x).strip() for x in dfr.iloc[i].tolist()]
            df = dfr.iloc[i + 1:].copy()
            df.columns = header
            out[nome] = df.reset_index(drop=True)
    return out

# ======================= PROCESSAMENTO =======================

def main():
    ap = argparse.ArgumentParser(description="Gera sazonal_base.json a partir do export de vendas.")
    ap.add_argument("arquivo", help="caminho do .xlsx de vendas (com várias abas)")
    ap.add_argument("-o", "--saida", default="sazonal_base.json", help="arquivo de saída (padrão: sazonal_base.json)")
    ap.add_argument("--sim", action="store_true", help="não perguntar confirmação (assume sim)")
    ap.add_argument("--diag", action="store_true", help="modo diagnóstico: mostra o formato de cada aba e sai")
    args = ap.parse_args()

    try:
        import pandas as pd
    except ImportError:
        print("ERRO: pandas não instalado. Rode:  pip install pandas openpyxl")
        sys.exit(1)

    if not os.path.exists(args.arquivo):
        print(f"ERRO: arquivo não encontrado: {args.arquivo}")
        sys.exit(1)

    if args.diag:
        print(f"\n=== DIAGNÓSTICO de {args.arquivo} ===")
        raw = _ler_raw(args.arquivo)
        for nome, dfr in raw.items():
            if dfr is None or len(dfr) == 0:
                print(f"\n--- Aba '{nome}': VAZIA ---")
                continue
            print(f"\n--- Aba '{nome}': {dfr.shape[0]} linhas x {dfr.shape[1]} colunas ---")
            for li in range(min(2, len(dfr))):
                vals = [str(x) for x in dfr.iloc[li].tolist()]
                print(f"  linha {li}:")
                for ci, v in enumerate(vals):
                    print(f"     [{ci}] {v[:45]}")
        print("\n=== fim do diagnóstico (cole esta saída para eu mapear as colunas) ===")
        sys.exit(0)

    posicional = COLUNAS_POR_POSICAO is not None
    print(f"\nLendo {args.arquivo} ...")
    if posicional:
        xls = _ler_raw(args.arquivo)        # layout fixo: lemos por posição, sem promover cabeçalho
    else:
        xls = carregar_abas(args.arquivo)
    print(f"Abas/tabelas encontradas: {len(xls)} -> {', '.join(xls.keys())}\n")
    if posicional:
        print("  Modo POR POSIÇÃO (layout fixo). Colunas usadas:")
        for k in ["produto", "filial", "data", "cfop", "quantidade", "nome"]:
            print(f"      {k:11s}: coluna [{COLUNAS_POR_POSICAO[k]}]")
        print()

    siglas = list(BASE_SIGLAS)            # ordem estável; novas siglas vão para o fim
    sig2idx = {s: i for i, s in enumerate(siglas)}

    # base[cod] = { idx_filial: [12 meses de float] }; nomes[cod] = nome
    base = {}
    nomes = {}
    # remessa[cod] = qtde total movimentada via CFOP 5116/5117/6116/6117 (não é venda, mas
    # comprova que o produto SAIU do estoque — usado para alertar "sem giro" suspeito).
    remessa = {}

    tot_linhas = tot_venda = tot_excluidas = tot_fora_periodo = tot_sem_data = tot_sem_prod = 0
    cfops_ignorados = {}
    primeira = True

    for nome_aba, df in xls.items():
        if df is None or df.empty:
            print(f"  Aba '{nome_aba}': vazia, pulando.")
            continue
        headers = list(df.columns)

        if posicional:
            col = dict(COLUNAS_POR_POSICAO)
            if max(col.values()) >= len(headers):
                print(f"  [aviso] aba '{nome_aba}' tem {len(headers)} colunas (esperado ≥ {max(col.values())+1}) — pulando.")
                continue
        else:
            col = {k: achar_coluna(headers, PISTAS[k], COLUNAS_MANUAIS[k]) for k in PISTAS}
            print(f"  Aba '{nome_aba}' ({len(df)} linhas) — colunas detectadas:")
            for k in ["produto", "filial", "data", "cfop", "quantidade", "nome"]:
                print(f"      {k:11s}: {col[k]}")
            faltando = [k for k in ("produto", "filial", "data", "cfop", "quantidade") if not col[k]]
            if faltando:
                print(f"  [ERRO] não consegui detectar: {faltando}.")
                print(f"  Colunas existentes no arquivo: {list(headers)}")
                print("  -> Copie o nome exato da coluna certa para o bloco COLUNAS_MANUAIS no topo do script e rode de novo.")
                print("  -> Se este for um relatório RESUMO (sem produto/data por linha), preciso do relatório DETALHADO de vendas.\n")
                sys.exit(2)

        # Confirmação só na primeira aba
        if primeira and not args.sim:
            try:
                amostra = df[[col['produto'], col['filial'], col['data'], col['cfop'], col['quantidade']]].head(3)
                print("  Amostra (3 primeiras linhas — produto, filial, data, cfop, qtde):")
                print(amostra.to_string(index=False))
            except Exception:
                pass
            print("\n  IMPORTANTE: a coluna 'data' deve ser a de EMISSÃO/VENDA (não a de entrega).")
            resp = input("  As colunas estão corretas? [s/N] ").strip().lower()
            if resp not in ("s", "sim", "y", "yes"):
                print("Cancelado. Ajuste a configuração no topo do script e rode novamente.")
                sys.exit(0)
            print()
        primeira = False

        cprod, cfil, cdata, ccfop, cqtd = col['produto'], col['filial'], col['data'], col['cfop'], col['quantidade']
        cnome = col['nome']

        for row in df.itertuples(index=False):
            tot_linhas += 1
            rd = dict(zip(headers, row))
            cfop = parse_cfop(rd.get(ccfop))
            if cfop in CFOPS_EXCLUIR:
                tot_excluidas += 1
                am_r = parse_ano_mes(rd.get(cdata))
                if am_r and no_periodo(*am_r):
                    cod_r = re.sub(r"\.0$", "", str(rd.get(cprod) or "").strip())
                    if cod_r and cod_r.lower() != "nan":
                        qtd_r = parse_qtd(rd.get(cqtd))
                        if qtd_r > 0:
                            remessa[cod_r] = remessa.get(cod_r, 0.0) + qtd_r
                continue
            if cfop not in CFOPS_VENDA:
                cfops_ignorados[cfop] = cfops_ignorados.get(cfop, 0) + 1
                continue
            am = parse_ano_mes(rd.get(cdata))
            if am is None:
                tot_sem_data += 1
                continue
            ano, mes = am
            if not no_periodo(ano, mes):
                tot_fora_periodo += 1
                continue
            cod = str(rd.get(cprod) or "").strip()
            cod = re.sub(r"\.0$", "", cod)
            if not cod or cod.lower() == "nan":
                tot_sem_prod += 1
                continue
            sigla = resolver_sigla(rd.get(cfil), siglas)
            if not sigla:
                continue
            if sigla not in sig2idx:
                sig2idx[sigla] = len(siglas)
                siglas.append(sigla)
            idx = sig2idx[sigla]
            qtd = parse_qtd(rd.get(cqtd))
            if qtd <= 0:
                continue
            tot_venda += 1
            base.setdefault(cod, {}).setdefault(idx, [0.0] * 12)[mes - 1] += qtd
            if cnome and cod not in nomes:
                nm = str(rd.get(cnome) or "").strip()
                if nm and nm.lower() != "nan":
                    nomes[cod] = nm

    # ---- Monta estrutura compacta: cod -> [geralSparse, {idx: mesesSparse}] ----
    def sparse(arr):
        return {str(i): int(round(v)) for i, v in enumerate(arr) if round(v) != 0}

    sazonal = {}
    for cod, filiais in base.items():
        geral = [0.0] * 12
        fil_sparse = {}
        for idx, meses in filiais.items():
            for i in range(12):
                geral[i] += meses[i]
            sp = sparse(meses)
            if sp:
                fil_sparse[str(idx)] = sp
        sazonal[cod] = [sparse(geral), fil_sparse]

    remessa_saida = {cod: int(round(v)) for cod, v in remessa.items() if round(v) != 0}

    saida = {
        "siglas": siglas,
        "ate": f"{PERIODO_FIM[0]:04d}-{PERIODO_FIM[1]:02d}",
        "fonte": os.path.basename(args.arquivo),
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "base": sazonal,
        "nomes": nomes,
        "remessa": remessa_saida,
    }

    with open(args.saida, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, separators=(",", ":"))

    tam_kb = os.path.getsize(args.saida) / 1024
    print("=" * 60)
    print("RESUMO")
    print(f"  Linhas lidas .............: {tot_linhas:,}".replace(",", "."))
    print(f"  Vendas contadas ..........: {tot_venda:,}".replace(",", "."))
    print(f"  Remessa entrega futura ...: {tot_excluidas:,} (excluídas)".replace(",", "."))
    print(f"  Produtos c/ remessa (CFOP 5116/5117/6116/6117): {len(remessa_saida):,}".replace(",", "."))
    print(f"  Fora do período ..........: {tot_fora_periodo:,}".replace(",", "."))
    print(f"  Sem data válida ..........: {tot_sem_data:,}".replace(",", "."))
    print(f"  Sem código de produto ....: {tot_sem_prod:,}".replace(",", "."))
    print(f"  Produtos na base .........: {len(sazonal):,}".replace(",", "."))
    print(f"  Filiais (siglas) .........: {len(siglas)}")
    if cfops_ignorados:
        top = sorted(cfops_ignorados.items(), key=lambda x: -x[1])[:10]
        print(f"  CFOPs ignorados (não-venda): {', '.join(f'{c}({n})' for c, n in top)}")
    print(f"\n  >> Gerado: {args.saida}  ({tam_kb:,.0f} KB)".replace(",", "."))
    print("     Envie SOMENTE esse arquivo (ou faça commit dele).")
    print("=" * 60)

if __name__ == "__main__":
    main()
