"""Coleta automatizada e reprodutível do recorte do projeto no Portal da Transparência.

Recorte: Bolsa Família, Auxílio Brasil e Novo Bolsa Família, de jan/2019 ao último mês publicado.

Uso (na raiz do repositório):
    python src/coleta.py listar                      # bases do recorte, meses publicados hoje e lacunas
    python src/coleta.py estimar                     # tamanho a baixar, sem baixar nada
    python src/coleta.py baixar                      # baixa e converte as três bases (cerca de 30 GB em ZIP)
    python src/coleta.py baixar --bases novo_bolsa_familia --inicio 2025
    python src/coleta.py referencias                 # IBGE (população, municípios, malhas) + tabela SIAFI
    python src/coleta.py resumo                      # metadados e reports/coleta_resumo.md
    python src/coleta.py benchmark                   # CSV × Parquet (tamanho e tempo de leitura)
    python src/coleta.py dicionario                  # docs/dicionario_dados.csv e .md

Rodar de novo o mesmo comando retoma de onde parou: o que já foi convertido é pulado,
downloads interrompidos continuam do ponto em que pararam.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import portal  # noqa: E402
from catalogo import BASES, INICIO_RECORTE, Base, faixa_periodo, normalizar_limite, selecionar  # noqa: E402
from util import (VERSAO_SCRIPT, Manifesto, Pastas, agora_iso, configurar_console, configurar_log,  # noqa: E402
                  espaco_livre, fmt_bytes, memoria_padrao, raiz_padrao, remover_arvore)

log = logging.getLogger("coleta")


# --------------------------------------------------------------------------- seleção e períodos

def _bases(args) -> list[Base]:
    # sem --bases, entram as três bases do recorte
    nomes = [b for item in (args.bases or []) for b in item.split(",") if b]
    return selecionar(nomes)


def _lacunas(periodos: list[str]) -> str:
    """Períodos que faltam entre o primeiro e o último publicados, em faixas ('201907, 202511-202601')."""
    mensais = sorted(p for p in periodos if len(p) == 6)
    faltam: list[str] = []
    if mensais:
        a, m = int(mensais[0][:4]), int(mensais[0][4:])
        tem = set(mensais)
        while f"{a:04d}{m:02d}" <= mensais[-1]:
            if f"{a:04d}{m:02d}" not in tem:
                faltam.append(f"{a:04d}{m:02d}")
            m += 1
            if m == 13:
                a, m = a + 1, 1
    if not faltam:
        return ""
    faixas, ini, ant = [], faltam[0], faltam[0]

    def prox(p):
        a, m = int(p[:4]), int(p[4:]) + 1
        return f"{a + (m == 13):04d}{1 if m == 13 else m:02d}"

    for p in faltam[1:] + [None]:
        if p is not None and p == prox(ant):
            ant = p
            continue
        faixas.append(ini if ini == ant else f"{ini}-{ant}")
        if p is not None:
            ini = ant = p
    return ", ".join(faixas)


def _gravar_por_base(caminho: Path, linhas: list[dict], bases: list[Base]) -> None:
    """Regrava `caminho` trocando só as linhas das `bases` desta execução e mantendo as demais.

    Assim `listar`, `estimar` e `baixar` com --bases não apagam o que outra execução já registrou.
    """
    from metadados import gravar_csv, ler_csv

    atuais = {b.chave for b in bases}
    antigas = [l for l in ler_csv(caminho) if l.get("base") not in atuais]
    ordem = {chave: i for i, chave in enumerate(BASES)}
    todas = sorted(antigas + linhas, key=lambda l: (ordem.get(l.get("base"), len(ordem)), str(l.get("periodo", ""))))
    campos = list(linhas[0].keys()) if linhas else (list(antigas[0].keys()) if antigas else None)
    gravar_csv(caminho, todas, campos)


def descobrir(bases: list[Base], pastas: Pastas) -> dict[str, list[str]]:
    """Períodos publicados hoje no Portal (com plano B no catálogo) e atualiza periodos_disponiveis.csv."""
    saida, linhas = {}, []
    for b in bases:
        periodos, origem = portal.descobrir_periodos(b)
        if periodos is None:
            log.warning("%s: não consegui ler a lista de arquivos no Portal (%s); usando a lista do catálogo "
                        "(conferida em 04/11/2026)", b.chave, origem)
            periodos, origem = list(b.periodos_referencia), "catalogo"
        # o Portal publica o Bolsa Família desde 2013, mas o recorte começa em jan/2019
        periodos = [p for p in periodos if p >= INICIO_RECORTE]
        novos = sorted(set(periodos) - set(b.periodos_referencia))
        saida[b.chave] = periodos
        linhas.append({
            "base": b.chave, "nome": b.nome, "origem_lista": origem, "n_periodos": len(periodos),
            "primeiro": periodos[0] if periodos else "", "ultimo": periodos[-1] if periodos else "", "lacunas": _lacunas(periodos),
            "novos_desde_catalogo": ", ".join(novos), "url_pagina": b.url_pagina,
            "url_dicionario": b.url_dicionario, "data_consulta": agora_iso(), "periodos": ", ".join(periodos),
        })
    _gravar_por_base(pastas.metadados / "periodos_disponiveis.csv", linhas, bases)
    return saida


def _filtrar(periodos: list[str], args) -> list[str]:
    ini = normalizar_limite(getattr(args, "inicio", None), fim=False)
    fim = normalizar_limite(getattr(args, "fim", None), fim=True)
    explicitos = {p for item in (getattr(args, "periodos", None) or []) for p in item.split(",") if p}
    saida = []
    for p in periodos:
        if explicitos and p not in explicitos:
            continue
        a, b = faixa_periodo(p)
        if ini and b < ini:
            continue
        if fim and a > fim:
            continue
        saida.append(p)
    return saida


# --------------------------------------------------------------------------- comandos

def cmd_listar(args, pastas: Pastas) -> int:
    bases = _bases(args)
    periodos = descobrir(bases, pastas)
    for b in bases:
        ps = periodos[b.chave]
        lac = _lacunas(ps)
        print(f"  {b.chave:<28} {len(ps):>4} arquivo(s)  {ps[0]} a {ps[-1]}" + (f"  | faltam: {lac}" if lac else ""))
    print(f"\nLista gravada em {pastas.metadados / 'periodos_disponiveis.csv'}")
    return 0


def cmd_estimar(args, pastas: Pastas) -> int:
    bases = _bases(args)
    periodos = descobrir(bases, pastas)
    # O que já foi baixado tem tamanho exato no manifesto: não precisa consultar o Portal de novo (que
    # bloqueia depois de ~50 consultas seguidas e deixa a estimativa de 1.200 arquivos levando horas).
    ultimos = Manifesto(pastas.metadados / "manifesto_coleta.jsonl").ultimos()
    linhas: list[dict] = []
    tarefas = []
    for b in bases:
        for p in _filtrar(periodos[b.chave], args):
            reg = ultimos.get((b.chave, p))
            if reg and reg.get("status") == "ok" and reg.get("bytes"):
                linhas.append({"base": b.chave, "periodo": p, "url": reg.get("url"), "url_final": reg.get("url_final"),
                               "http_status": 200, "bytes_zip": int(reg["bytes"]), "ultima_modificacao": None,
                               "tentativas": 0, "erro": None, "origem": "manifesto"})
            else:
                tarefas.append((b, p))
    log.info("%d arquivo(s) com tamanho conhecido do manifesto; consultando o tamanho de %d no Portal (só "
             "cabeçalhos HTTP, nada é baixado; 2 consultas por vez com pausa, porque o Portal bloqueia rajadas)…",
             len(linhas), len(tarefas))
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="estimar") as ex:
            for i, linha in enumerate(ex.map(lambda bp: portal.tamanho_remoto(*bp), tarefas), 1):
                linhas.append({**linha, "origem": "portal"})
                if i % 50 == 0:
                    log.info("  %d/%d consultados", i, len(tarefas))
    except KeyboardInterrupt:
        portal.PARAR.set()
        log.warning("Interrompido; gravando o que já foi consultado.")
    _gravar_por_base(pastas.metadados / "estimativa_volume.csv", linhas, bases)
    total, sem, bloqueados = 0, 0, 0
    print(f"\n{'base':<28} {'arquivos':>8} {'ZIP total':>12} {'maior ZIP':>12}")
    for b in bases:
        ls = [l for l in linhas if l["base"] == b.chave]
        tam = [l["bytes_zip"] for l in ls if l["bytes_zip"]]
        sem += sum(1 for l in ls if not l["bytes_zip"])
        bloqueados += sum(1 for l in ls if l["http_status"] in portal.STATUS_TEMPORARIOS)
        total += sum(tam)
        print(f"{b.chave:<28} {len(ls):>8} {fmt_bytes(sum(tam)):>12} {fmt_bytes(max(tam) if tam else 0):>12}")
    print(f"{'TOTAL':<28} {len(linhas):>8} {fmt_bytes(total):>12}")
    if sem:
        print(f"\n{sem} arquivo(s) sem tamanho informado (indisponíveis ou servidor não informou).")
    if bloqueados:
        print(f"{bloqueados} deles porque o Portal bloqueou as consultas (HTTP 405/429/5xx) mesmo depois das "
              f"repetições: espere alguns minutos e rode `estimar` de novo para essas bases; o total acima "
              f"está subestimado.")
    print("\nO espaço final em Parquet costuma ficar na ordem do tamanho dos ZIPs; durante a conversão o "
          "script precisa, além disso, de espaço livre para o maior CSV descompactado.")
    print(f"Detalhe por arquivo: {pastas.metadados / 'estimativa_volume.csv'}")
    return 0


def _ja_concluido(reg: dict | None, pastas: Pastas, opcoes: dict) -> bool:
    if not reg or reg.get("status") != "ok":
        return False
    if (reg.get("opcoes") or {}) != opcoes:
        return False
    return all((pastas.raiz / a).exists() for t in reg.get("tabelas", []) for a in t.get("arquivos_parquet", []))


def cmd_baixar(args, pastas: Pastas) -> int:
    import conversao
    import metadados

    bases = _bases(args)
    opcoes = conversao.OpcoesConversao(
        memoria=args.memoria or memoria_padrao(), threads=args.threads,
        ufs=tuple(u.strip().upper() for item in (args.ufs or []) for u in item.split(",") if u.strip()),
        remover_nomes=args.remover_nomes, min_livre=int(args.min_livre_gb * 1024**3))
    manifesto = Manifesto(pastas.metadados / "manifesto_coleta.jsonl")
    ultimos = manifesto.ultimos()
    periodos = descobrir(bases, pastas)

    tarefas, puladas = [], 0
    for b in bases:
        for p in _filtrar(periodos[b.chave], args):
            if not args.forcar and _ja_concluido(ultimos.get((b.chave, p)), pastas, opcoes.assinatura()):
                puladas += 1
                continue
            if (not args.forcar and not args.repetir_indisponiveis
                    and (ultimos.get((b.chave, p)) or {}).get("status") == "indisponivel"):
                puladas += 1
                continue
            tarefas.append((b, p))
    log.info("Plano: %d arquivo(s) a processar, %d já concluído(s) ou indisponível(is) pulado(s). "
             "Disco livre: %s. Memória para o DuckDB: %s.", len(tarefas), puladas,
             fmt_bytes(espaco_livre(pastas.raiz)), opcoes.memoria or "padrão")
    if not tarefas:
        metadados.atualizar(pastas)
        log.info("Nada a fazer. Metadados atualizados em %s", pastas.metadados)
        return 0

    pastas.downloads.mkdir(parents=True, exist_ok=True)
    falhas: list[str] = []
    ok = 0
    t0 = time.monotonic()
    simultaneos = max(1, args.simultaneos)
    fila = deque()
    pendentes = iter(enumerate(tarefas, 1))

    def enfileirar(ex):
        try:
            i, (b, p) = next(pendentes)
        except StopIteration:
            return
        fila.append((i, b, p, ex.submit(portal.baixar, b, p, pastas.downloads, args.tentativas,
                                         opcoes.min_livre, args.pausa)))

    interrompido = False
    ex = ThreadPoolExecutor(max_workers=simultaneos, thread_name_prefix="download")
    try:
        for _ in range(simultaneos + 1):
            enfileirar(ex)
        while fila:
            i, b, p, fut = fila.popleft()
            try:
                while True:  # espera em fatias de 1 s para o Ctrl+C funcionar também no Windows
                    try:
                        res = fut.result(timeout=1)
                        break
                    except FuturesTimeout:
                        continue
            except Exception as e:  # noqa: BLE001  (erro inesperado no download não derruba a coleta)
                res = portal.ResultadoDownload(base=b.chave, periodo=p, url=b.url_download(p, portal.URL_BASE),
                                               erro=f"{type(e).__name__}: {e}", data_download=agora_iso())
            if not portal.PARAR.is_set():
                enfileirar(ex)
            registro = {
                "base": b.chave, "periodo": p, "url": res.url, "url_final": res.url_final, "status": res.status,
                "http_status": res.http_status, "tentativas": res.tentativas, "retomado": res.retomado,
                "bytes": res.bytes, "sha256": res.sha256, "segundos": res.segundos,
                "data_download": res.data_download, "erro": res.erro, "opcoes": opcoes.assinatura(),
                "versao_script": VERSAO_SCRIPT, "tabelas": [],
            }
            if res.status == "ok":
                log.info("[%d/%d] %s %s: baixado %s em %.0fs; convertendo…", i, len(tarefas), b.chave, p,
                         fmt_bytes(res.bytes), res.segundos)
                try:
                    registro["tabelas"] = conversao.processar(res.caminho, res.tipo, b, p, pastas, opcoes)
                    ok += 1
                    if args.manter_zip:
                        pastas.zips.mkdir(parents=True, exist_ok=True)
                        res.caminho.replace(pastas.zips / res.caminho.name)
                    else:
                        res.caminho.unlink(missing_ok=True)
                except Exception as e:  # noqa: BLE001
                    registro["status"] = "erro"
                    registro["erro"] = f"conversão: {type(e).__name__}: {e}"
                    falhas.append(f"{b.chave} {p}: {registro['erro']}")
                    if isinstance(e, zipfile.BadZipFile) or "CRC" in str(e):
                        res.caminho.unlink(missing_ok=True)  # ZIP corrompido: baixa de novo na próxima execução
                        log.error("[%d/%d] %s %s: ZIP corrompido (%s); será baixado de novo na próxima execução",
                                  i, len(tarefas), b.chave, p, e)
                    else:
                        log.exception("[%d/%d] %s %s: falha na conversão (o ZIP fica em %s para a próxima "
                                      "tentativa)", i, len(tarefas), b.chave, p, pastas.downloads)
            elif res.status == "indisponivel":
                log.warning("[%d/%d] %s %s: indisponível no Portal (%s)", i, len(tarefas), b.chave, p, res.erro)
            else:
                falhas.append(f"{b.chave} {p}: {res.erro}")
                log.error("[%d/%d] %s %s: %s", i, len(tarefas), b.chave, p, res.erro)
            if res.status != "interrompido":
                manifesto.adicionar(registro)
            if portal.PARAR.is_set():
                break
    except KeyboardInterrupt:
        portal.PARAR.set()
        interrompido = True
        log.warning("Interrompido. Rode o mesmo comando de novo para continuar de onde parou.")
    finally:
        if fila:
            portal.PARAR.set()
        ex.shutdown(wait=True, cancel_futures=True)
        remover_arvore(pastas.tmp / "csv")
        remover_arvore(pastas.tmp / "parquet")
    totais = metadados.atualizar(pastas)
    dur = time.monotonic() - t0
    log.info("Fim: %d convertido(s) nesta execução em %.0f min. Acumulado: %s linhas, ZIP %s, CSV %s, "
             "Parquet %s.", ok, dur / 60, f"{totais['n_linhas']:,}".replace(",", "."),
             fmt_bytes(totais["bytes_zip"]), fmt_bytes(totais["bytes_csv"]), fmt_bytes(totais["bytes_parquet"]))
    if falhas:
        log.error("%d arquivo(s) com problema (rode de novo para tentar outra vez):\n  %s", len(falhas),
                  "\n  ".join(falhas[:50]))
        return 1
    return 130 if interrompido else 0


def cmd_referencias(args, pastas: Pastas) -> int:
    import metadados
    import referencias

    metas = referencias.coletar(pastas, sem_malhas=args.sem_malhas)
    metadados.gravar_csv(pastas.metadados / "referencias.csv", metas)
    erros = [m for m in metas if m["status"] != "ok"]
    log.info("Segunda fonte: %d item(ns) ok, %d com erro. Detalhes em %s", len(metas) - len(erros), len(erros),
             pastas.metadados / "referencias.csv")
    return 1 if any(m["fonte"].startswith(("IBGE/SIDRA", "kelvins")) for m in erros) else 0


def cmd_resumo(args, pastas: Pastas) -> int:
    import metadados

    t = metadados.atualizar(pastas)
    red = 100 * (1 - t["bytes_parquet"] / t["bytes_csv"]) if t["bytes_csv"] else 0
    print(f"Arquivos baixados: {t['arquivos_baixados']}  |  linhas: {t['n_linhas']:,}".replace(",", "."))
    print(f"ZIP {fmt_bytes(t['bytes_zip'])}  |  CSV {fmt_bytes(t['bytes_csv'])}  |  Parquet "
          f"{fmt_bytes(t['bytes_parquet'])}  |  redução CSV→Parquet {f'{red:.1f}'.replace('.', ',')}%")
    print(f"Tabelas: {pastas.metadados}  |  resumo para o relatório: {pastas.reports / 'coleta_resumo.md'}")
    return 0


def cmd_benchmark(args, pastas: Pastas) -> int:
    import benchmark
    import metadados

    linhas = benchmark.executar(_bases(args), pastas, args.limite_mb)
    if linhas:
        metadados.gravar_csv(pastas.metadados / "benchmark_formatos.csv", linhas, benchmark.CAMPOS)
    metadados.atualizar(pastas)
    log.info("Benchmark gravado em %s", pastas.metadados / "benchmark_formatos.csv")
    return 0


def cmd_dicionario(args, pastas: Pastas) -> int:
    import dicionario

    linhas = dicionario.executar(_bases(args), pastas, args.amostra, args.n_periodos)
    log.info("Dicionário com %d variáveis em %s e %s", len(linhas), pastas.docs / "dicionario_dados.csv",
             pastas.docs / "dicionario_dados.md")
    return 0


def cmd_tudo(args, pastas: Pastas) -> int:
    codigo = cmd_referencias(args, pastas)
    codigo |= cmd_baixar(args, pastas)
    cmd_benchmark(args, pastas)
    cmd_dicionario(args, pastas)
    cmd_resumo(args, pastas)
    return codigo


# --------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python src/coleta.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raiz", type=Path, default=raiz_padrao(), help="raiz do repositório (padrão: pasta acima de src/)")
    p.add_argument("--log", default="INFO", help="nível de log no console (DEBUG, INFO, WARNING)")
    sub = p.add_subparsers(dest="comando", required=True)

    def selecao(sp):
        sp.add_argument("--bases", action="append",
                        help=f"só estas bases, separadas por vírgula (padrão: as três): {', '.join(BASES)}")

    def periodo(sp):
        sp.add_argument("--inicio", help="primeiro período (AAAA ou AAAAMM)")
        sp.add_argument("--fim", help="último período (AAAA ou AAAAMM)")
        sp.add_argument("--periodos", action="append", help="lista explícita de períodos (ex.: 202401,202402)")

    sp = sub.add_parser("listar", help="bases e períodos publicados hoje no Portal")
    selecao(sp)

    sp = sub.add_parser("estimar", help="tamanho total a baixar (sem baixar)")
    selecao(sp)
    periodo(sp)

    def opcoes_baixar(sp):
        sp.add_argument("--simultaneos", type=int, default=2, help="downloads simultâneos (padrão 2)")
        sp.add_argument("--tentativas", type=int, default=6, help="tentativas por arquivo (padrão 6)")
        sp.add_argument("--pausa", type=float, default=1.0, help="pausa entre downloads, em segundos")
        sp.add_argument("--forcar", action="store_true", help="reprocessa mesmo o que já foi convertido")
        sp.add_argument("--repetir-indisponiveis", action="store_true",
                        help="tenta de novo períodos que deram 404 em execuções anteriores")
        sp.add_argument("--manter-zip", action="store_true", help="guarda os ZIPs em data/_zips (ocupa espaço)")
        sp.add_argument("--memoria", help="limite de memória do DuckDB, ex.: 4GB (padrão: metade da RAM)")
        sp.add_argument("--threads", type=int, help="threads do DuckDB (padrão: todos os núcleos)")
        sp.add_argument("--ufs", action="append", help="grava só estas UFs (ex.: PR,SC); o download continua nacional")
        sp.add_argument("--remover-nomes", action="store_true",
                        help="não grava as colunas com nome de pessoas (nome do favorecido)")
        sp.add_argument("--min-livre-gb", type=float, default=5.0,
                        help="para antes de deixar o disco com menos que isso livre (padrão 5)")

    sp = sub.add_parser("baixar", help="baixa e converte para Parquet (retoma de onde parou)")
    selecao(sp)
    periodo(sp)
    opcoes_baixar(sp)

    sp = sub.add_parser("referencias", help="segunda fonte: IBGE (população, municípios, malhas) e tabela SIAFI")
    sp.add_argument("--sem-malhas", action="store_true", help="não baixa as malhas (≈240 MB)")

    sub.add_parser("resumo", help="regrava as tabelas de metadados e reports/coleta_resumo.md")

    sp = sub.add_parser("benchmark", help="CSV × Parquet: tamanho e tempo de leitura")
    selecao(sp)
    sp.add_argument("--limite-mb", type=int, default=400, help="maior CSV lido com pandas (padrão 400 MB)")

    sp = sub.add_parser("dicionario", help="dicionário de dados (oficial + perfil observado)")
    selecao(sp)
    sp.add_argument("--amostra", type=int, default=500_000, help="linhas amostradas por tabela")
    sp.add_argument("--n-periodos", type=int, default=6, help="quantos períodos recentes entram na amostra")

    sp = sub.add_parser("tudo", help="referencias + baixar + benchmark + dicionario + resumo")
    selecao(sp)
    periodo(sp)
    opcoes_baixar(sp)
    sp.add_argument("--sem-malhas", action="store_true")
    sp.add_argument("--limite-mb", type=int, default=400)
    sp.add_argument("--amostra", type=int, default=500_000)
    sp.add_argument("--n-periodos", type=int, default=6)
    return p


def main(argv: list[str] | None = None) -> int:
    configurar_console()
    args = _parser().parse_args(argv)
    pastas = Pastas(args.raiz.resolve())
    pastas.criar()
    configurar_log(pastas, args.comando, args.log)
    log.info("coleta.py %s | comando: %s | raiz: %s", VERSAO_SCRIPT, args.comando, pastas.raiz)
    comandos = {"listar": cmd_listar, "estimar": cmd_estimar, "baixar": cmd_baixar,
                "referencias": cmd_referencias, "resumo": cmd_resumo, "benchmark": cmd_benchmark,
                "dicionario": cmd_dicionario, "tudo": cmd_tudo}
    try:
        return comandos[args.comando](args, pastas)
    except KeyboardInterrupt:
        portal.PARAR.set()
        log.warning("Interrompido pelo usuário.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
