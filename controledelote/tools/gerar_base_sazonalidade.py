#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador da BASE DE SAZONALIDADE para o Controle de Auditoria de Lote.

O que faz
---------
Lê o export bruto de vendas (arquivo .xlsx grande, com VÁRIAS abas), filtra os
CFOP de venda (conta o MÊS QUE VENDEU, não o que entregou), agrega por
PRODUTO x FILIAL x MÊS e cospe um arquivo PEQUENO (sazonal_base.json) no mesmo
formato compacto que o app usa. A planilha grande NUNCA sai da sua máquina.

COMO USAR (versão rápida e de memória baixa)
--------------------------------------------
1) Instale o Python 3.9+ e, UMA vez só, no Prompt:      py -m pip install openpyxl lxml
   (lxml = leitor rápido próprio; sem ele o script usa só o openpyxl, que funciona mas é ~4x mais lento)
2) Coloque este arquivo na MESMA PASTA da planilha (.xlsx) e dê DOIS CLIQUES nele
   (ou rode:  py gerar_base_sazonalidade.py ). Ele acha a planilha sozinho.
   - Se houver mais de uma planilha na pasta, ele usa a MAIOR e avisa qual.
   - Para escolher outra:  py gerar_base_sazonalidade.py "C:\\caminho\\vendas.xlsx"
3) Ele mostra uma amostra, pede confirmação (Enter = sim) e gera "sazonal_base.json"
   na mesma pasta. Esse arquivo é pequeno: é só ele que você envia.

Por que esta versão não trava com planilha de centenas de MB
------------------------------------------------------------
A versão antiga carregava TODAS as abas inteiras na memória como texto (e ainda fazia
uma cópia) — para 400 MB isso pede dezenas de GB e leva horas. Esta lê em FLUXO,
linha por linha, guardando só as 6 colunas necessárias. O pandas só é usado para arquivos
.xls antigos ou HTML disfarçado de .xls.

Opções:  --sim (não pergunta)   --diag (mostra o formato das abas e sai)   -o saida.json
         --motor openpyxl  (leitor lento e simples, se o rápido der algum problema)

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
- Período mantido: desde Jan/2024 até o mês atual — o limite superior é calculado
  automaticamente a cada execução (mês da data do computador), para nunca ficar
  desatualizado e cortar vendas recentes por engano.
- O campo "ate" do JSON de saída reflete o mês da VENDA MAIS RECENTE realmente
  encontrada nos dados (não o limite da janela), para o painel mostrar a cobertura
  real do arquivo importado.
- Sazonalidade medida em UNIDADES (soma da quantidade), por mês (Jan=0 ... Dez=11).
"""

import sys, os, json, re, argparse, time, glob, itertools, traceback
from datetime import datetime, date

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
PROGRESSO_A_CADA = 250_000      # imprime andamento a cada N linhas lidas

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

# Janela de histórico mantida (ano, mês) inclusive.
# PERIODO_FIM é calculado a partir da data do computador (mês atual) toda vez que o
# script roda — não fique mais travado num mês fixo do passado, senão vendas mais
# novas que essa data são descartadas como "fora do período" (foi o que aconteceu
# com o corte fixo em Jun/2026, escondendo vendas de Jul/2026 em diante).
PERIODO_INI = (2024, 1)
PERIODO_FIM = (date.today().year, date.today().month)

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
    if isinstance(v, bool): return 0.0
    if isinstance(v, (int, float)):
        return float(v) if v == v else 0.0                    # (v == v) descarta NaN
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
    Aceita 5102, 5102.0, '5.102' e '5102 - 00 - VENDA MERC ADQ'."""
    if v is None: return ""
    if isinstance(v, float) and v.is_integer(): v = int(v)
    s = str(v).strip()
    token = re.split(r"[\s\-–]+", s, maxsplit=1)[0]   # "5102" de "5102 - 00 - VENDA..."
    d = re.sub(r"\D", "", token)
    if len(d) >= 4:
        return d[:4]
    m = re.search(r"\d{4}", re.sub(r"(?<=\d)[.\s](?=\d)", "", s))  # fallback: 1º grupo de 4 dígitos
    return m.group(0) if m else d

def parse_ano_mes(v):
    """Retorna (ano, mes) 1-12, ou None. Aceita datetime, serial Excel (número) e strings pt-BR."""
    if v is None: return None
    if isinstance(v, (datetime, date)):
        return (v.year, v.month)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 <= v < 80000:                                # serial do Excel
            dt = datetime.fromordinal(datetime(1899, 12, 30).toordinal() + int(v))
            return (dt.year, dt.month)
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "nan": return None
    # serial do Excel como texto (ex.: "45292" ou "45292.0")
    if re.fullmatch(r"\d{5}(\.0+)?", s):
        try:
            dt = datetime.fromordinal(datetime(1899, 12, 30).toordinal() + int(float(s)))
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

def _txt(v):
    """Texto de uma célula: número inteiro vindo como 21.0 vira '21'; None vira ''."""
    if v is None: return ""
    if isinstance(v, float) and v == v and v.is_integer():
        return str(int(v))
    return str(v).strip()

def resolver_sigla(v, siglas_idx):
    """Converte o conteúdo da coluna filial em sigla de 3 letras."""
    if v is None: return None
    s = _txt(v).upper()
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

# ======================= LEITURA EM FLUXO (xlsx) =======================

def abas_xlsx(path, max_col):
    """xlsx/xlsm em STREAMING: memória constante. Gera (nome_aba, linhas, largura_declarada, headers=None)."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("ERRO: falta o pacote 'openpyxl'. Rode uma vez no Prompt:\n    py -m pip install openpyxl")
        sys.exit(1)
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            try:
                largura = ws.max_column
            except Exception:
                largura = None
            try:
                ws.reset_dimensions()                       # não depende da dimensão declarada no arquivo
            except Exception:
                pass
            yield ws.title, ws.iter_rows(values_only=True, max_col=max_col), largura, None
    finally:
        wb.close()

class LeitorIncompativel(Exception):
    pass

_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_COL = {}

def _col(letras):
    i = _COL.get(letras)
    if i is None:
        n = 0
        for ch in letras:
            n = n * 26 + (ord(ch) - 64)
        i = _COL[letras] = n - 1
    return i

def _ler_strings(z, etree):
    """Tabela de textos compartilhados do xlsx (junta <t> e <r><t>; ignora a fonética <rPh>)."""
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    out = []
    with z.open("xl/sharedStrings.xml") as f:
        for _, si in etree.iterparse(f, events=("end",), tag="{*}si"):
            partes = []
            for ch in si:
                tg = ch.tag.rsplit("}", 1)[-1]
                if tg == "t":
                    partes.append(ch.text or "")
                elif tg == "r":
                    for t in ch:
                        if t.tag.rsplit("}", 1)[-1] == "t":
                            partes.append(t.text or "")
            out.append("".join(partes))
            si.clear()
            while si.getprevious() is not None:
                del si.getparent()[0]
    return out

def _listar_abas(z, etree):
    wb = etree.fromstring(z.read("xl/workbook.xml"))
    rels = etree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    alvo = {r.get("Id"): r.get("Target") for r in rels}
    abas = []
    for sh in wb.iter("{*}sheet"):
        t = (alvo.get(sh.get("{%s}id" % _NS_REL)) or "").lstrip("/")
        abas.append((sh.get("name"), t if t.startswith("xl/") else "xl/" + t))
    return abas

def _linhas_rapido(z, etree, caminho, strings, ncols, necessarias, com_numero=False):
    """Gera tuplas com as primeiras 'ncols' colunas de cada linha, em fluxo (memória constante).
    Só converte o valor das colunas em 'necessarias' (None = todas)."""
    V = IS_T = None
    with z.open(caminho) as f:
        for _, row in etree.iterparse(f, events=("end",), tag="{*}row"):
            if V is None:
                ns = row.tag[: row.tag.index("}") + 1]
                V, IS_T = ns + "v", ns + "t"
            vals = [None] * ncols if ncols else []
            seq = 0
            for c in row:
                ref = c.get("r")
                idx = _col(ref.rstrip("0123456789")) if ref else seq
                seq = idx + 1
                if ncols and idx >= ncols:
                    break
                if necessarias is not None and idx not in necessarias:
                    continue
                t = c.get("t")
                if t == "inlineStr":
                    x = "".join(e.text or "" for e in c.iter(IS_T))
                else:
                    v = c.find(V)
                    if v is None or v.text is None:
                        continue
                    x = v.text
                    if t == "s":
                        x = strings[int(x)]
                    elif t == "str":
                        pass
                    elif t == "b":
                        x = (x == "1")
                    elif t == "e":
                        x = None
                    else:                                  # número (as datas também são números seriais)
                        x = float(x) if ("." in x or "E" in x or "e" in x) else int(x)
                if not ncols:
                    while len(vals) <= idx:
                        vals.append(None)
                vals[idx] = x
            yield (int(row.get("r") or 0), tuple(vals)) if com_numero else tuple(vals)
            row.clear()
            while row.getprevious() is not None:
                del row.getparent()[0]

def _igual(a, b):
    """Compara o valor do leitor rápido (a) com o do openpyxl (b); datas: compara ano/mês."""
    if b is None or b == "":
        return a is None or a == ""
    if isinstance(b, (datetime, date)):
        return isinstance(a, (int, float)) and parse_ano_mes(a) == (b.year, b.month)
    if isinstance(b, bool) or isinstance(a, bool):
        return a == b
    if isinstance(b, (int, float)) and isinstance(a, (int, float)):
        return abs(a - b) < 1e-9
    return a == b

def abas_rapido(path, max_col, necessarias):
    """xlsx lido por um leitor próprio em fluxo: ~4x mais rápido que o openpyxl e com memória CONSTANTE
    (uns 20 MB, não importa o tamanho). Antes de começar, CONFERE as primeiras linhas contra o openpyxl;
    se algo divergir, levanta LeitorIncompativel e o script usa o openpyxl."""
    import zipfile
    try:
        from lxml import etree
    except ImportError:
        raise LeitorIncompativel("falta o pacote lxml (py -m pip install lxml)")
    z = zipfile.ZipFile(path)
    strings = _ler_strings(z, etree)
    abas = _listar_abas(z, etree)
    if not abas:
        raise LeitorIncompativel("não achei abas no arquivo")
    try:
        from openpyxl import load_workbook
    except ImportError:
        load_workbook = None
    with z.open(abas[0][1]) as f:                           # o openpyxl só é barato se a aba declara a dimensão
        tem_dimensao = re.search(rb"<(\w+:)?dimension", f.read(16384)) is not None
    if load_workbook is not None and not tem_dimensao:
        print("  [info] a planilha não declara o tamanho das abas: conferência com o openpyxl PULADA (seria lenta).\n"
              "         Confira a amostra que será mostrada abaixo.")
        load_workbook = None
    if load_workbook is not None:                           # auto-verificação nas 1ªs 300 linhas da 1ª aba
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ref = {i + 1: r for i, r in enumerate(wb[abas[0][0]].iter_rows(min_row=1, max_row=300, values_only=True, max_col=max_col))}
            vistos = set()
            for num, vals in _linhas_rapido(z, etree, abas[0][1], strings, max_col, necessarias, com_numero=True):
                if num > 300:
                    break
                vistos.add(num)
                r = ref.get(num, ())
                for j in (necessarias if necessarias is not None else range(len(vals))):
                    a = vals[j] if j < len(vals) else None
                    b = r[j] if j < len(r) else None
                    if not _igual(a, b):
                        raise LeitorIncompativel(f"linha {num}, coluna {j}: leitor rápido={a!r} / openpyxl={b!r}")
            for num, r in ref.items():
                if num not in vistos and any(r[j] not in (None, "") for j in (necessarias or range(len(r))) if j < len(r)):
                    raise LeitorIncompativel(f"linha {num} existe no openpyxl e não no leitor rápido")
        finally:
            wb.close()
    return _gerar_abas_rapido(z, etree, strings, abas, max_col, necessarias)

def _gerar_abas_rapido(z, etree, strings, abas, max_col, necessarias):
    for nome, xml in abas:
        yield nome, _linhas_rapido(z, etree, xml, strings, max_col, necessarias), None, None

def calamine_ok():
    try:
        import python_calamine  # noqa: F401
        return True
    except ImportError:
        return False

def abas_calamine(path):
    """xlsx lido pelo python-calamine (Rust): ~15x mais rápido que o openpyxl. Carrega UMA aba por vez.
    O calamine só devolve a área usada (se a coluna A for vazia, as colunas deslocariam): compensamos."""
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_path(path)
    for nome in wb.sheet_names:
        sh = wb.get_sheet_by_name(nome)
        try:
            off_col = sh.start[1] if sh.start else 0
        except Exception:
            off_col = 0
        largura = (sh.width + off_col) if sh.width else 0
        linhas = sh.iter_rows()
        if off_col:
            pad = [None] * off_col
            linhas = (pad + r for r in linhas)
        yield nome, linhas, largura, None
        del sh

def abas_legado(path, posicional):
    """.xls binário / HTML disfarçado: não dá para ler em fluxo (carrega tudo, como a versão antiga)."""
    try:
        xls = _ler_raw(path) if posicional else carregar_abas(path)
    except MemoryError:
        print("ERRO: arquivo grande demais para este formato. Abra no Excel e salve como .xlsx (que é lido em fluxo).")
        sys.exit(1)
    for nome, df in xls.items():
        if df is None or df.empty:
            yield nome, iter(()), 0, None
            continue
        yield nome, df.itertuples(index=False, name=None), df.shape[1], (None if posicional else [str(c) for c in df.columns])

def eh_xlsx(path):
    with open(path, "rb") as f:
        return f.read(2) == b"PK"

def descobrir_arquivo(caminho):
    """Se não foi informado, procura a planilha na MESMA PASTA deste script."""
    if caminho:
        return caminho
    cand = []
    for ext in ("*.xlsx", "*.xlsm", "*.xls"):
        for f in glob.glob(os.path.join(PASTA_SCRIPT, ext)):
            nome = os.path.basename(f).lower()
            if nome.startswith("~$") or nome.startswith("sazonal_base"):
                continue                                    # arquivo temporário do Excel / saída anterior
            cand.append(f)
    if not cand:
        print(f"ERRO: não achei nenhuma planilha (.xlsx / .xls) na pasta:\n    {PASTA_SCRIPT}\n"
              "Coloque a planilha de vendas na MESMA pasta deste script, ou informe o caminho:\n"
              '    py gerar_base_sazonalidade.py "C:\\caminho\\vendas.xlsx"')
        sys.exit(1)
    cand.sort(key=os.path.getsize, reverse=True)
    if len(cand) > 1:
        print("Encontrei mais de uma planilha na pasta (uso a MAIOR):")
        for f in cand:
            print(f"    {os.path.getsize(f) / 1024 / 1024:9.1f} MB  {os.path.basename(f)}")
        print()
    return cand[0]

def mapear_colunas(nome_aba, linhas, headers):
    """Devolve ({campo: índice}, linhas). Por posição (padrão) ou detectando o cabeçalho pelo nome."""
    if COLUNAS_POR_POSICAO is not None:
        return dict(COLUNAS_POR_POSICAO), linhas
    if headers is None:                                     # xlsx em fluxo: acha o cabeçalho nas 1ªs 25 linhas
        buf = list(itertools.islice(linhas, 25))
        if not buf:
            return None, linhas
        melhor_i, melhor_s = 0, -1
        for i, r in enumerate(buf):
            sc = _score_keys(["" if x is None else x for x in r])
            if sc > melhor_s:
                melhor_s, melhor_i = sc, i
        headers = ["" if x is None else str(x).strip() for x in buf[melhor_i]]
        linhas = itertools.chain(buf[melhor_i + 1:], linhas)
    col = {k: achar_coluna(headers, PISTAS[k], COLUNAS_MANUAIS[k]) for k in PISTAS}
    print(f"  Aba '{nome_aba}' — colunas detectadas:")
    for k in ["produto", "filial", "data", "cfop", "quantidade", "nome"]:
        print(f"      {k:11s}: {col[k]}")
    faltando = [k for k in ("produto", "filial", "data", "cfop", "quantidade") if not col[k]]
    if faltando:
        print(f"  [ERRO] não consegui detectar: {faltando}.")
        print(f"  Colunas existentes no arquivo: {list(headers)}")
        print("  -> Copie o nome exato da coluna certa para o bloco COLUNAS_MANUAIS no topo do script e rode de novo.")
        sys.exit(2)
    return {k: (headers.index(v) if v is not None else None) for k, v in col.items()}, linhas

# ======================= PROCESSAMENTO =======================

def main():
    ap = argparse.ArgumentParser(description="Gera sazonal_base.json a partir do export de vendas.")
    ap.add_argument("arquivo", nargs="?", default=None,
                    help="caminho do .xlsx de vendas (se omitido, procura na pasta deste script)")
    ap.add_argument("-o", "--saida", default=os.path.join(PASTA_SCRIPT, "sazonal_base.json"),
                    help="arquivo de saída (padrão: sazonal_base.json ao lado do script)")
    ap.add_argument("--sim", action="store_true", help="não perguntar confirmação (assume sim)")
    ap.add_argument("--motor", choices=("auto", "rapido", "calamine", "openpyxl"), default="auto",
                    help="leitor do xlsx: auto/rapido (leitor próprio, memória constante), calamine (mais rápido, mas usa MUITA memória), openpyxl (lento)")
    ap.add_argument("--diag", action="store_true", help="modo diagnóstico: mostra o formato de cada aba e sai")
    args = ap.parse_args()

    arquivo = descobrir_arquivo(args.arquivo)
    if not os.path.exists(arquivo):
        print(f"ERRO: arquivo não encontrado: {arquivo}")
        sys.exit(1)
    tam_mb = os.path.getsize(arquivo) / 1024 / 1024
    print(f"Planilha: {arquivo}  ({tam_mb:,.0f} MB)".replace(",", "."))

    xlsx = eh_xlsx(arquivo)

    if args.diag:
        print(f"\n=== DIAGNÓSTICO de {arquivo} ===")
        if xlsx:
            from openpyxl import load_workbook
            wb = load_workbook(arquivo, read_only=True, data_only=True)
            for ws in wb.worksheets:
                print(f"\n--- Aba '{ws.title}': dimensão declarada {ws.max_row} linhas x {ws.max_column} colunas ---")
                for li, r in enumerate(ws.iter_rows(max_row=2, values_only=True)):
                    print(f"  linha {li}:")
                    for ci, v in enumerate(r):
                        print(f"     [{ci}] {str(v)[:45]}")
            wb.close()
        else:
            for nome, dfr in _ler_raw(arquivo).items():
                print(f"\n--- Aba '{nome}': {dfr.shape[0]} linhas x {dfr.shape[1]} colunas ---")
                for li in range(min(2, len(dfr))):
                    print(f"  linha {li}:")
                    for ci, v in enumerate(dfr.iloc[li].tolist()):
                        print(f"     [{ci}] {str(v)[:45]}")
        print("\n=== fim do diagnóstico (cole esta saída para eu mapear as colunas) ===")
        return 0

    posicional = COLUNAS_POR_POSICAO is not None
    max_col = (max(COLUNAS_POR_POSICAO.values()) + 1) if posicional else None
    if xlsx:
        motor = "rapido" if args.motor == "auto" else args.motor
        necessarias = set(COLUNAS_POR_POSICAO.values()) if posicional else None
        abas = None
        if motor == "rapido":
            try:
                abas = abas_rapido(arquivo, max_col, necessarias)
                print("Leitor: rápido em fluxo (memória constante); primeiras linhas conferidas contra o openpyxl: OK.")
            except LeitorIncompativel as e:
                print(f"[aviso] leitor rápido não usado ({e}).\n        Usando o openpyxl (mais lento, mas seguro).")
                motor = "openpyxl"
        if motor == "calamine":
            if not calamine_ok():
                print("ERRO: --motor calamine pedido, mas falta o pacote. Rode:  py -m pip install python-calamine")
                sys.exit(1)
            print("Leitor: python-calamine (muito rápido, mas carrega a aba inteira na memória: ~5 GB por 1 milhão de linhas).")
            abas = abas_calamine(arquivo)
        if abas is None:
            print("Leitor: openpyxl em fluxo (memória baixa, porém lento).")
            abas = abas_xlsx(arquivo, max_col)
    else:
        print("Lendo (formato antigo: carrega tudo na memória)...")
        abas = abas_legado(arquivo, posicional)
    if posicional:
        print("  Modo POR POSIÇÃO (layout fixo). Colunas usadas:")
        for k in ["produto", "filial", "data", "cfop", "quantidade", "nome"]:
            print(f"      {k:11s}: coluna [{COLUNAS_POR_POSICAO[k]}]")
        print()

    siglas = list(BASE_SIGLAS)            # ordem estável; novas siglas vão para o fim
    sig2idx = {s: i for i, s in enumerate(siglas)}
    base, nomes, remessa = {}, {}, {}
    tot_linhas = tot_venda = tot_excluidas = tot_fora_periodo = tot_sem_data = tot_sem_prod = 0
    maior_am = None       # (ano, mes) da venda mais recente realmente contada
    cfops_ignorados = {}
    primeira = True
    t0 = time.time()
    n_abas = 0

    for nome_aba, linhas, largura, headers in abas:
        n_abas += 1
        idx, linhas = mapear_colunas(nome_aba, linhas, headers)
        if idx is None:
            print(f"  Aba '{nome_aba}': vazia, pulando.")
            continue
        if posicional and largura and largura < max(idx.values()) + 1:
            print(f"  [aviso] aba '{nome_aba}' tem {largura} colunas (esperado ≥ {max(idx.values())+1}) — pulando.")
            continue
        iprod, ifil, idata, icfop, iqtd, inome = (idx["produto"], idx["filial"], idx["data"],
                                                  idx["cfop"], idx["quantidade"], idx["nome"])

        if primeira and not args.sim:
            amostra = list(itertools.islice(linhas, 4))
            print(f"  Aba '{nome_aba}' — amostra (primeiras linhas: produto | filial | data | cfop | qtde):")
            for r in amostra:
                g = lambda i: (r[i] if i is not None and i < len(r) else None)
                print("     ", " | ".join(str(g(i))[:28] for i in (iprod, ifil, idata, icfop, iqtd)))
            print("\n  IMPORTANTE: a coluna 'data' deve ser a de EMISSÃO/VENDA (não a de entrega).")
            try:
                resp = input("  As colunas estão corretas? [S/n] ").strip().lower()
            except EOFError:
                resp = "s"
            if resp in ("n", "nao", "não", "no"):
                print("Cancelado. Ajuste a configuração no topo do script e rode novamente.")
                return 0
            print()
            linhas = itertools.chain(amostra, linhas)
        primeira = False

        print(f"  Aba '{nome_aba}' ...")
        n_aba = 0
        for row in linhas:
            n_aba += 1
            tot_linhas += 1
            if tot_linhas % PROGRESSO_A_CADA == 0:
                dec = time.time() - t0
                print(f"     {tot_linhas:>11,} linhas lidas | {tot_venda:,} vendas contadas | {tot_linhas/dec:,.0f} linhas/s | {dec/60:.1f} min".replace(",", "."))
            n = len(row)
            cfop = parse_cfop(row[icfop] if icfop < n else None)
            if cfop in CFOPS_EXCLUIR:
                tot_excluidas += 1
                am_r = parse_ano_mes(row[idata] if idata < n else None)
                if am_r and no_periodo(*am_r):
                    cod_r = re.sub(r"\.0$", "", _txt(row[iprod] if iprod < n else None))
                    if cod_r and cod_r.lower() != "nan":
                        qtd_r = parse_qtd(row[iqtd] if iqtd < n else None)
                        if qtd_r > 0:
                            remessa[cod_r] = remessa.get(cod_r, 0.0) + qtd_r
                continue
            if cfop not in CFOPS_VENDA:
                cfops_ignorados[cfop or "(vazio)"] = cfops_ignorados.get(cfop or "(vazio)", 0) + 1
                continue
            am = parse_ano_mes(row[idata] if idata < n else None)
            if am is None:
                tot_sem_data += 1
                continue
            ano, mes = am
            if not no_periodo(ano, mes):
                tot_fora_periodo += 1
                continue
            cod = re.sub(r"\.0$", "", _txt(row[iprod] if iprod < n else None))
            if not cod or cod.lower() == "nan":
                tot_sem_prod += 1
                continue
            sigla = resolver_sigla(row[ifil] if ifil < n else None, siglas)
            if not sigla:
                continue
            if sigla not in sig2idx:
                sig2idx[sigla] = len(siglas)
                siglas.append(sigla)
            idxf = sig2idx[sigla]
            qtd = parse_qtd(row[iqtd] if iqtd < n else None)
            if qtd <= 0:
                continue
            tot_venda += 1
            if maior_am is None or (ano, mes) > maior_am:
                maior_am = (ano, mes)
            base.setdefault(cod, {}).setdefault(idxf, [0.0] * 12)[mes - 1] += qtd
            if inome is not None and cod not in nomes:
                nm = _txt(row[inome] if inome < n else None)
                if nm and nm.lower() != "nan":
                    nomes[cod] = nm
        print(f"     {n_aba:,} linhas".replace(",", "."))

    if tot_linhas == 0:
        print("ERRO: nenhuma linha lida. O arquivo está vazio ou em formato inesperado (tente --diag).")
        return 1
    if tot_venda == 0:
        print("\n[ATENÇÃO] Nenhuma venda foi contada. Confira COLUNAS_POR_POSICAO / o período no topo do script (rode com --diag).")

    # ---- Monta estrutura compacta: cod -> [geralSparse, {idx: mesesSparse}] ----
    def sparse(arr):
        return {str(i): int(round(v)) for i, v in enumerate(arr) if round(v) != 0}

    sazonal = {}
    for cod, filiais in base.items():
        geral = [0.0] * 12
        fil_sparse = {}
        for idxf, meses in filiais.items():
            for i in range(12):
                geral[i] += meses[i]
            sp = sparse(meses)
            if sp:
                fil_sparse[str(idxf)] = sp
        sazonal[cod] = [sparse(geral), fil_sparse]

    remessa_saida = {cod: int(round(v)) for cod, v in remessa.items() if round(v) != 0}

    # "ate" reflete o mês da venda MAIS RECENTE de fato encontrada no arquivo (não o
    # limite da janela PERIODO_FIM) — é o que o painel exibe como cobertura da base.
    ate_real = f"{maior_am[0]:04d}-{maior_am[1]:02d}" if maior_am else f"{PERIODO_FIM[0]:04d}-{PERIODO_FIM[1]:02d}"

    saida = {
        "siglas": siglas,
        "ate": ate_real,
        "fonte": os.path.basename(arquivo),
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "base": sazonal,
        "nomes": nomes,
        "remessa": remessa_saida,
    }

    with open(args.saida, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, separators=(",", ":"))

    tam_kb = os.path.getsize(args.saida) / 1024
    dec = time.time() - t0
    print("=" * 60)
    print("RESUMO")
    print(f"  Abas lidas ...............: {n_abas}")
    print(f"  Linhas lidas .............: {tot_linhas:,}".replace(",", "."))
    print(f"  Vendas contadas ..........: {tot_venda:,}".replace(",", "."))
    print(f"  Remessa entrega futura ...: {tot_excluidas:,} (excluídas)".replace(",", "."))
    print(f"  Produtos c/ remessa (CFOP 5116/5117/6116/6117): {len(remessa_saida):,}".replace(",", "."))
    print(f"  Fora do período ..........: {tot_fora_periodo:,}".replace(",", "."))
    print(f"  Sem data válida ..........: {tot_sem_data:,}".replace(",", "."))
    print(f"  Sem código de produto ....: {tot_sem_prod:,}".replace(",", "."))
    print(f"  Produtos na base .........: {len(sazonal):,}".replace(",", "."))
    print(f"  Filiais (siglas) .........: {len(siglas)}")
    print(f"  Vendas até ...............: {ate_real}")
    if cfops_ignorados:
        top = sorted(cfops_ignorados.items(), key=lambda x: -x[1])[:10]
        print(f"  CFOPs ignorados (não-venda): {', '.join(f'{c}({n})' for c, n in top)}")
    print(f"  Tempo ....................: {dec/60:.1f} min")
    print(f"\n  >> Gerado: {args.saida}  ({tam_kb:,.0f} KB)".replace(",", "."))
    print("     Envie SOMENTE esse arquivo (ou faça commit dele).")
    print("=" * 60)
    return 0

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    pausar = len(sys.argv) == 1          # duplo clique / sem argumentos: mantém a janela aberta no fim
    codigo = 0
    try:
        codigo = main() or 0
    except SystemExit as e:
        codigo = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        codigo = 130
    except PermissionError as e:
        print(f"\nERRO: sem permissão para ler/gravar: {e}\nSe a planilha estiver aberta no Excel, feche-a e rode de novo.")
        codigo = 1
    except MemoryError:
        print("\nERRO: faltou memória. Feche outros programas ou salve a planilha como .xlsx e tente de novo.")
        codigo = 1
    except Exception:
        traceback.print_exc()
        codigo = 1
    if pausar:
        try:
            input("\nPressione ENTER para fechar...")
        except EOFError:
            pass
    sys.exit(codigo)
