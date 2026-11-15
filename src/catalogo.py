"""Catálogo das bases do Portal da Transparência usadas no projeto.

O recorte são as três fases do programa federal de transferência de renda, de jan/2019 até o último mês
publicado: Bolsa Família (até nov/2021), Auxílio Brasil (nov/2021 a fev/2023) e Novo Bolsa Família (a partir
de mar/2023). Cada base tem:
- a "chave" interna (nome da pasta em data/bronze);
- o "slug" usado na URL de download:  /download-de-dados/<slug>/<periodo>;
- a "página" que lista os arquivos disponíveis (de onde o script descobre os meses publicados);
- os meses que estavam publicados quando o catálogo foi conferido (04/11/2026), usados só como plano B se a
  página do Portal mudar e a descoberta automática falhar.
"""

from dataclasses import dataclass

URL_PORTAL = "https://portaldatransparencia.gov.br"
DATA_CONFERENCIA = "2026-11-04"
INICIO_RECORTE = "201901"  # o recorte começa em jan/2019


def _expandir(faixa):
    # '201901-202111' -> ('201901', '201902', ..., '202111')
    inicio, fim = faixa.split("-")
    ano = int(inicio[:4])
    mes = int(inicio[4:])
    meses = []
    while f"{ano:04d}{mes:02d}" <= fim:
        meses.append(f"{ano:04d}{mes:02d}")
        mes += 1
        if mes == 13:
            ano += 1
            mes = 1
    return tuple(meses)


@dataclass(frozen=True)
class Base:
    chave: str
    slug: str
    nome: str
    pagina: str
    dicionario: str
    periodos_referencia: tuple
    observacao: str = ""

    @property
    def url_pagina(self):
        return f"{URL_PORTAL}/download-de-dados/{self.pagina}"

    @property
    def url_dicionario(self):
        return f"{URL_PORTAL}{self.dicionario}"

    def url_download(self, periodo, url_portal=URL_PORTAL):
        return f"{url_portal}/download-de-dados/{self.slug}/{periodo}"


_BASES = [
    Base("bolsa_familia_pagamentos", "bolsa-familia-pagamentos", "Bolsa Família - Pagamentos",
         "bolsa-familia-pagamentos", "/pagina-interna/603397-dicionario-de-dados-bolsa-familia-pagamentos",
         _expandir("201901-202111"),
         observacao="O Portal publica a série desde jan/2013, mas o recorte começa em jan/2019. O arquivo de "
                    "nov/2021 repete os pagamentos do Auxílio Brasil e traz os rótulos de competência e "
                    "referência trocados (ver a EDA)."),
    Base("auxilio_brasil", "auxilio-brasil", "Auxílio Brasil",
         "auxilio-brasil", "/pagina-interna/605516-dicionario-de-dados-auxilio-brasil-saques",
         _expandir("202111-202302"),
         observacao="Substituiu o Bolsa Família entre nov/2021 e fev/2023. No dicionário, MÊS COMPETÊNCIA e "
                    "MÊS REFERÊNCIA têm significados trocados em relação ao Bolsa Família e ao Novo Bolsa Família, "
                    "mas os dados não seguem essa troca: a competência é o mês do arquivo, como nas outras bases "
                    "(conferido na EDA)."),
    Base("novo_bolsa_familia", "novo-bolsa-familia", "Novo Bolsa Família",
         "novo-bolsa-familia", "/pagina-interna/605543-dicionario-de-dados-novo-bolsa-familia",
         _expandir("202303-202609")),
]

BASES = {}
for _base in _BASES:
    BASES[_base.chave] = _base


def selecionar(nomes=None):
    # sem --bases, entram as três bases do recorte
    if not nomes:
        return list(_BASES)
    escolhidas = []
    for nome in nomes:
        chave = nome.strip().replace("-", "_")
        if chave not in BASES:
            raise SystemExit(f"Base desconhecida: {nome}. Use uma de: {', '.join(BASES)}")
        escolhidas.append(chave)
    return [b for b in _BASES if b.chave in escolhidas]


def faixa_periodo(periodo):
    # mês do Portal (AAAAMM) como início e fim, para filtrar por --inicio/--fim
    return periodo[:6], periodo[:6]


def normalizar_limite(valor, fim):
    if not valor:
        return None
    valor = valor.replace("-", "").replace("/", "")
    if len(valor) == 4:
        return valor + ("12" if fim else "01")
    if len(valor) == 6:
        return valor
    raise SystemExit(f"Use AAAA ou AAAAMM em --inicio/--fim (recebi {valor}).")
