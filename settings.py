import os
from decouple import config
import pandas as pd
SERVER_OLD = config('SERVER_OLD')
DATABASE_OLD = config('DATABASE_OLD')
USERNAME_OLD = config('DBUSERNAME_OLD')
PASSWORD_OLD = config('PASSWORD_OLD')

HEADER_KEY = config('HEADER_KEY')
AUTH_KEY_GENERAL = config('AUTH_KEY_GENERAL')


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

CONTACT_BUTTON_URL = "https://wa.me/55318009419333?text=Oi%20quero%20regularizar%20minha%20dívida!"

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