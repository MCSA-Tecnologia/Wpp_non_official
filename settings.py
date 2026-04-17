import os
from decouple import config
import pandas as pd
SERVER = config('SERVER')
DATABASE = config('DATABASE')
USERNAME = config('DBUSERNAME')
PASSWORD = config('PASSWORD')

SERVER_OLD = config('SERVER_OLD')
DATABASE_OLD = config('DATABASE_OLD')
USERNAME_OLD = config('DBUSERNAME_OLD')
PASSWORD_OLD = config('PASSWORD_OLD')

HEADER_KEY = config('HEADER_KEY')
AUTH_KEY_GENERAL = config('AUTH_KEY_GENERAL')

RO_CALLTECH_ENDPOINT = config(
    'RO_CALLTECH_ENDPOINT',
    default='https://calltechsmart.kinghost.net/portal/registrarResumo'
)
RO_TIMEOUT_SECONDS = config('RO_TIMEOUT_SECONDS', default=60, cast=int)
RO_TRIGGER_MIN_COUNT = config('RO_TRIGGER_MIN_COUNT', default=100, cast=int)
RO_BATCH_SIZE = config('RO_BATCH_SIZE', default=390, cast=int)
RO_RESUMO_ID = config('RO_RESUMO_ID', default=12, cast=int)
RO_OPERADOR_ID = config('RO_OPERADOR_ID', default=227, cast=int)
RO_CODIGO_CAMPANHA = config('RO_CODIGO_CAMPANHA', default='000000')
RO_CAMPANHA_ID = config('RO_CAMPANHA_ID', default=0, cast=int)
RO_ORIGEM = config('RO_ORIGEM', default='API - Whatsapp Unofficial')
RO_PARCEIRO = config('RO_PARCEIRO', default='API Whatsapp Unofficial')


QUERY_CLIENTS_PHONE = """wwwwselect distinct top(100w0)
	M.MoInadimplentesID,
	dbo.RetornaNomeRazaoSocial(M.MoInadimplentesID)Cliente,
	PC.PesDDD + PC.PesTelefone Telefone,
	sum(MoValorDocumento)Valor,
	DATEDIFF(d,min(MoDataVencimento),getdate())Aging
from Candiotto_STD.dbo.Movimentacoes M
	inner join Candiotto_STD.dbo.PessoasContatos PC on M.MoInadimplentesID = PC.PesPessoasID
where
	M.MoCampanhasID in (33,74)
	and M.MoStatusMovimentacao = 0
	and M.MoDataVencimento < getdate()
	and M.MoOrigemMovimentacao in ('I','C')
	and not exists (
		SELECT 1
		FROM Candiotto_STD.dbo.Movimentacoes mA
		WHERE mA.MoInadimplentesID    = m.MoInadimplentesID
		  and mA.MoCampanhasID        = m.MoCampanhasID
		  and mA.MoOrigemMovimentacao = 'A'
		  and mA.MoStatusMovimentacao = 0
	)
	AND (PesTelefoneInativo = 0 OR PesTelefoneInativo IS NULL)
    AND PesTelefone IS NOT NULL
    AND PesTelefone <> ''
    AND LEN(PesTelefone) = 9
    AND LEFT(PesTelefone, 1) = '9'
    AND LEN(PesDDD) = 2
group by
	M.MoInadimplentesID,
	dbo.RetornaNomeRazaoSocial(M.MoInadimplentesID),
	PC.PesDDD + PC.PesTelefone
order by 5 asc, 4 desc"""

CONTACT_BUTTON_URL = "https://wa.me/55318009419333?text=Oi%20quero%20regularizar%20minha%20divida"

CONTACT_MESSAGE = ("""Bom dia NOME_DO_CLIENTE, 
constatamos que há uma dívida aberta no seu nome.
Entre em contato para regularizar a situação.

MCSA - Marcelo Candiotto Sociedade de advogados""")

df = pd.DataFrame(
    [
        ["31 9137-6705", 0, True]
    ],
    columns=["Telefone", "col2", "col3"]
)
