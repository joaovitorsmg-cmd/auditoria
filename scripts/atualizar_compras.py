"""
Atualiza automaticamente o painel Análise de Compra Agrícola.
- Faz login no intranet, baixa os 2 relatórios XLS
- Converte para dados.json
- Faz commit via GitHub API (sem precisar de git local)

Dependências: pip install playwright openpyxl requests
              playwright install chromium
"""

import asyncio
import base64
import configparser
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


# ── CONFIG ────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.ini"


def carregar_config():
    if not CONFIG_PATH.exists():
        print("[ERRO] config.ini não encontrado. Copie config.exemplo.ini para config.ini e preencha.")
        sys.exit(1)
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg


# ── DOWNLOAD DOS RELATÓRIOS VIA PLAYWRIGHT ────────────────────────────────────
async def baixar_relatorios(cfg):
    from playwright.async_api import async_playwright

    usuario = cfg["intranet"]["usuario"]
    senha   = cfg["intranet"]["senha"]
    linha   = cfg["relatorio"].get("linha_produto", "5")

    # Período CEF: de 01/01 do ano atual até hoje
    hoje     = datetime.now()
    dt_ini   = f"01/01/{hoje.year}"
    dt_fim   = hoje.strftime("%d/%m/%Y")

    tmpdir = Path(tempfile.mkdtemp())
    arq_est = None
    arq_cef = None

    print(f"[INFO] Iniciando navegador... período CEF: {dt_ini} → {dt_fim}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page()

        # LOGIN
        print("[1/4] Fazendo login...")
        await page.goto("http://intra.agroquima.com.br/agr/login.jsf", wait_until="networkidle")
        await page.fill("#loginForm\\:login", usuario)
        await page.fill("#loginForm\\:senha", senha)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("networkidle")

        # PLANILHA ESTOQUE
        print("[2/4] Baixando Estoque...")
        await page.goto(
            "http://intra.agroquima.com.br/agr/estoqueJSP/consultaEstoqueProdutoQtdApp.jsf",
            wait_until="networkidle"
        )
        # Seleciona 2ª opção de tipo de consulta (por filial)
        try:
            await page.locator("#formPrincipal\\:pnFiltros td:nth-of-type(2) > label").click()
            await page.wait_for_timeout(500)
        except Exception:
            pass

        # Linha Produto = 5
        campo_est = page.locator("#formPrincipal\\:inputLinhaProduto\\:inputLinhaProdutoInput")
        await campo_est.click()
        await campo_est.fill(linha)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(800)

        # Consultar
        await page.locator("#formPrincipal\\:btnConsultar").click()
        await page.wait_for_load_state("networkidle")

        # Download XLS
        async with page.expect_download(timeout=60000) as dl_info:
            await page.locator("#formPrincipal\\:tabelaLinha\\:j_idt210").click()
        dl = await dl_info.value
        arq_est = tmpdir / "estoque.xls"
        await dl.save_as(str(arq_est))
        print(f"   ✓ Estoque salvo: {arq_est.name}")

        # PLANILHA CEF
        print("[3/4] Baixando CEF...")
        await page.goto(
            "http://intra.agroquima.com.br/agr/estoqueJSP/controleEntregaFuturaApp.jsf",
            wait_until="networkidle"
        )
        # Selecionar todas as filiais
        try:
            await page.locator("#formPrincipal\\:checkFilial_label").click()
            await page.wait_for_timeout(300)
            await page.locator("div.ui-widget-header > div.ui-chkbox span").click()
            await page.wait_for_timeout(300)
        except Exception:
            pass

        # Data início
        campo_ini = page.locator("#formPrincipal\\:j_idt224_input")
        await campo_ini.triple_click()
        await campo_ini.fill(dt_ini)

        # Data fim
        campo_fim = page.locator("#formPrincipal\\:j_idt226_input")
        await campo_fim.click()
        await page.wait_for_timeout(300)

        # Formato XLS
        try:
            await page.locator("#formPrincipal\\:j_idt238_label").click()
            await page.wait_for_timeout(300)
            await page.locator("#formPrincipal\\:j_idt238_1").click()
            await page.wait_for_timeout(300)
        except Exception:
            pass

        # Linha Produto = 5
        campo_cef = page.locator("#formPrincipal\\:LinhaProduto\\:LinhaProdutoInput")
        await campo_cef.click()
        await campo_cef.fill(linha)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(800)

        # Gerar Relatório
        async with page.expect_download(timeout=120000) as dl_info2:
            await page.locator("#formPrincipal\\:j_idt296").click()
        dl2 = await dl_info2.value
        arq_cef = tmpdir / "cef.xls"
        await dl2.save_as(str(arq_cef))
        print(f"   ✓ CEF salvo: {arq_cef.name}")

        await browser.close()

    return arq_cef, arq_est


# ── CONVERTER XLS → JSON ──────────────────────────────────────────────────────
EXCLUIR = {13, 24, 36}
RM = {1:'SUL',3:'NORTE',4:'SUL',5:'SUL',10:'NORTE',11:'SUL',12:'SUL',14:'NORTE',
      16:'NORTE',17:'MT',19:'NORTE',21:'SUL',22:'NORTE',23:'NORTE',25:'MT',
      27:'SUL',28:'SUL',29:'MT',30:'SUL',31:'SUL',32:'NORTE',34:'SUL',35:'MT',
      37:'MT',38:'NORTE',40:'SUL',41:'NORTE'}


def ler_xls(caminho):
    """Lê XLS antigo (.xls) com openpyxl fallback xlrd."""
    try:
        import xlrd
        wb = xlrd.open_workbook(str(caminho))
        ws = wb.sheet_by_index(0)
        headers = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]
        rows = []
        for r in range(1, ws.nrows):
            rows.append({headers[c]: ws.cell_value(r, c) for c in range(ws.ncols)})
        return rows
    except Exception:
        import openpyxl
        wb = openpyxl.load_workbook(str(caminho), read_only=True, data_only=True)
        ws = wb.active
        rows_raw = list(ws.iter_rows(values_only=True))
        if not rows_raw:
            return []
        headers = [str(h).strip() if h is not None else "" for h in rows_raw[0]]
        return [dict(zip(headers, r)) for r in rows_raw[1:]]


def processar_cef(caminho):
    rows = ler_xls(caminho)
    resultado = []
    for r in rows:
        fp = int(float(r.get("Filial Ped.") or r.get("Filial Ped") or 0))
        if fp not in RM or fp in EXCLUIR:
            continue
        saldo_raw = r.get("Saldo ") or r.get("Saldo") or 0
        try:
            saldo = float(str(saldo_raw).replace(",", ".")) if isinstance(saldo_raw, str) else float(saldo_raw or 0)
        except Exception:
            saldo = 0.0
        if saldo <= 0:
            continue
        resultado.append({
            "filial": fp,
            "codigo": int(float(r.get("Produto") or 0)),
            "desc": str(r.get("Descrição Produto") or r.get("Descricao Produto") or "").strip(),
            "saldo": saldo,
            "forn": str(r.get("Fornecedor") or "").strip(),
        })
    return [r for r in resultado if r["filial"] and r["codigo"]]


def processar_estoque(caminho):
    rows = ler_xls(caminho)
    resultado = {}
    for r in rows:
        fil_str = str(r.get("Filial") or "")
        import re
        m = re.match(r"^(\d+)", fil_str)
        if not m:
            continue
        fil = int(m.group(1))
        if fil in EXCLUIR or fil not in RM:
            continue
        cod = int(float(r.get("Código") or r.get("Codigo") or 0))
        if not cod:
            continue
        qtde_raw = r.get("Qtde. Estoque") or r.get("Qtde Estoque") or 0
        try:
            qtde = float(str(qtde_raw).replace(",", ".")) if isinstance(qtde_raw, str) else float(qtde_raw or 0)
        except Exception:
            qtde = 0.0
        key = f"{fil}_{cod}"
        if key not in resultado or qtde > resultado[key]:
            resultado[key] = qtde
    return resultado


# ── PUSH VIA GITHUB API ───────────────────────────────────────────────────────
def github_push(cfg, dados_json: str):
    token  = cfg["github"]["token"]
    owner  = cfg["github"].get("owner", "joaovitorsmg-cmd")
    repo   = cfg["github"].get("repo", "auditoria")
    path   = cfg["github"].get("path", "analise-compra-agricola/data/dados.json")
    branch = cfg["github"].get("branch", "main")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    # Obter SHA atual (necessário para atualização)
    sha = None
    r = requests.get(api_url, headers=headers, params={"ref": branch})
    if r.status_code == 200:
        sha = r.json().get("sha")

    payload = {
        "message": f"Atualiza dados compras agrícolas ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "content": base64.b64encode(dados_json.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    r2 = requests.put(api_url, headers=headers, json=payload)
    if r2.status_code in (200, 201):
        print(f"[OK] Dados publicados no GitHub ({r2.status_code})")
    else:
        print(f"[ERRO] GitHub API: {r2.status_code} — {r2.text[:200]}")
        sys.exit(1)


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  Atualização Automática — Análise de Compra Agrícola")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    cfg = carregar_config()

    # Download
    arq_cef, arq_est = await baixar_relatorios(cfg)

    # Processar
    print("[4/4] Processando planilhas...")
    cef_data = processar_cef(arq_cef)
    est_data = processar_estoque(arq_est)
    print(f"   CEF: {len(cef_data)} registros | Estoque: {len(est_data)} chaves")

    dados = {
        "geradoEm": datetime.now().isoformat(),
        "cef": cef_data,
        "est": est_data,
    }
    dados_json = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))

    # Push
    github_push(cfg, dados_json)
    print("\n✅ Concluído! O painel será atualizado automaticamente.")


if __name__ == "__main__":
    asyncio.run(main())
