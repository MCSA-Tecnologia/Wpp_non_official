"""
Central configuration. Everything comes from .env (python-decouple),
with safe defaults so the project runs even with a minimal .env.
"""
from decouple import config

# ---------------------------------------------------------------------------
# General / dispatch
# ---------------------------------------------------------------------------
MAX_ACCOUNTS = config("MAX_ACCOUNTS", default=6, cast=int)

# Delay between messages per account (milliseconds). A random value inside
# the window is used for every send to avoid a robotic constant cadence.
MIN_SEND_DELAY_MS = config("MIN_SEND_DELAY_MS", default=20000, cast=int)
MAX_SEND_DELAY_MS = config("MAX_SEND_DELAY_MS", default=45000, cast=int)

# How long each sender waits after the last message for delivery ACKs.
ACK_GRACE_MS = config("ACK_GRACE_MS", default=30000, cast=int)

# Watchdog: sender exits with error if nothing happens for this long (0 = off)
SEND_INACTIVITY_TIMEOUT_MS = config("SEND_INACTIVITY_TIMEOUT_MS", default=300000, cast=int)

# Auth phase timeout (seconds) waiting for the QR scan.
AUTH_TIMEOUT_SECONDS = config("AUTH_TIMEOUT_SECONDS", default=240, cast=int)

# Extra attempts when an account fails to authenticate (fresh QR each retry).
AUTH_MAX_RETRIES = config("AUTH_MAX_RETRIES", default=2, cast=int)

# Validate the number on WhatsApp before sending (recommended: True).
VALIDATE_NUMBERS = config("VALIDATE_NUMBERS", default=True, cast=bool)

DEFAULT_COUNTRY_CODE = config("DEFAULT_COUNTRY_CODE", default="55")

# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------
CONTACT_MESSAGE = config(
    "CONTACT_MESSAGE",
    default=(
        "Bom dia NOME_DO_CLIENTE,\n"
        "constatamos que há uma pendência aberta no seu nome.\n"
        "Entre em contato para regularizar a situação.\n\n"
        "MCSA - Marcelo Candiotto Sociedade de Advogados"
    ),
)

CONTACT_BUTTON_URL = config(
    "CONTACT_BUTTON_URL",
    default="https://wa.me/55318009419333?text=Oi%20quero%20regularizar%20minha%20divida",
)

# ---------------------------------------------------------------------------
# Database (optional — only needed when loading contacts from SQL Server)
# ---------------------------------------------------------------------------
SERVER = config("SERVER", default="")
DATABASE = config("DATABASE", default="")
USERNAME = config("DBUSERNAME", default="")
PASSWORD = config("PASSWORD", default="")
ODBC_DRIVER = config("ODBC_DRIVER", default="ODBC Driver 17 for SQL Server")

QUERY_CLIENTS_PHONE = config("QUERY_CLIENTS_PHONE", default="""
select distinct top(1000)
    M.MoInadimplentesID as pessoaId,
    dbo.RetornaNomeRazaoSocial(M.MoInadimplentesID) as Nome,
    PC.PesDDD + PC.PesTelefone as Telefone,
    sum(MoValorDocumento) as Valor,
    DATEDIFF(d, min(MoDataVencimento), getdate()) as Aging
from Candiotto_STD.dbo.Movimentacoes M
    inner join Candiotto_STD.dbo.PessoasContatos PC
        on M.MoInadimplentesID = PC.PesPessoasID
where
    M.MoCampanhasID in (33, 74)
    and M.MoStatusMovimentacao = 0
    and M.MoDataVencimento < getdate()
    and M.MoOrigemMovimentacao in ('I', 'C')
    and not exists (
        select 1
        from Candiotto_STD.dbo.Movimentacoes mA
        where mA.MoInadimplentesID    = M.MoInadimplentesID
          and mA.MoCampanhasID        = M.MoCampanhasID
          and mA.MoOrigemMovimentacao = 'A'
          and mA.MoStatusMovimentacao = 0
    )
    and (PesTelefoneInativo = 0 or PesTelefoneInativo is null)
    and PesTelefone is not null
    and PesTelefone <> ''
    and LEN(PesTelefone) = 9
    and LEFT(PesTelefone, 1) = '9'
    and LEN(PesDDD) = 2
group by
    M.MoInadimplentesID,
    dbo.RetornaNomeRazaoSocial(M.MoInadimplentesID),
    PC.PesDDD + PC.PesTelefone
order by 5 asc, 4 desc
""")

# Query used by the "Carregar Credores/Campanhas" button in the frontend.
QUERY_CREDOR_CAMPANHA = config("QUERY_CREDOR_CAMPANHA", default="""
SELECT DISTINCT
    [CAMPANHA],
    [CREDOR]
FROM [Candiotto_DBA].[dbo].[tabelatitulos]
WHERE [CREDOR] IS NOT NULL
  AND [CAMPANHA] IS NOT NULL
ORDER BY [CREDOR], [CAMPANHA]
""")

# ---------------------------------------------------------------------------
# RO / Calltech
# ---------------------------------------------------------------------------
RO_ENABLED = config("RO_ENABLED", default=True, cast=bool)
RO_CALLTECH_ENDPOINT = config(
    "RO_CALLTECH_ENDPOINT",
    default="https://calltechsmart.kinghost.net/portal/registrarResumo",
)
RO_TIMEOUT_SECONDS = config("RO_TIMEOUT_SECONDS", default=60, cast=int)
RO_TRIGGER_MIN_COUNT = config("RO_TRIGGER_MIN_COUNT", default=100, cast=int)
RO_BATCH_SIZE = config("RO_BATCH_SIZE", default=390, cast=int)
RO_RESUMO_ID = config("RO_RESUMO_ID", default=12, cast=int)
RO_OPERADOR_ID = config("RO_OPERADOR_ID", default=227, cast=int)
RO_CODIGO_CAMPANHA = config("RO_CODIGO_CAMPANHA", default="000000")
RO_CAMPANHA_ID = config("RO_CAMPANHA_ID", default=0, cast=int)
RO_ORIGEM = config("RO_ORIGEM", default="API - Whatsapp Unofficial")
RO_PARCEIRO = config("RO_PARCEIRO", default="API Whatsapp Unofficial")

# ---------------------------------------------------------------------------
# Error / success reporting endpoints (optional)
# ---------------------------------------------------------------------------
ERROR_REPORT_URL = config("ERROR_REPORT_URL", default="")
ERROR_REPORT_AUTH_TOKEN = config("ERROR_REPORT_AUTH_TOKEN", default="")
ERROR_REPORT_HEADER_KEY = config("ERROR_REPORT_HEADER_KEY", default="")
ERROR_REPORT_HEADER_VALUE = config("ERROR_REPORT_HEADER_VALUE", default="")

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
FRONTEND_HOST = config("FRONTEND_HOST", default="127.0.0.1")
FRONTEND_PORT = config("FRONTEND_PORT", default=8502, cast=int)
