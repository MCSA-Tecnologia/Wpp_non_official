/* Clientes com título ORIGINAL em aberto, SEM acordo, nas campanhas selecionadas,
   sem ação digital nos últimos 20 dias, COM algum telefone cadastrado.
   >>> 2000 sorteados, PRIORIZANDO quem tem telefone principal;
       completa com não-principal só se faltar para fechar 2000. */
WITH Base AS (
    SELECT DISTINCT
        M.MoInadimplentesID AS PessoaID,
        M.MoClientesID      AS CredorID,
        M.MoCampanhasID     AS CampanhaID
    FROM [Candiotto_STD].[dbo].[Movimentacoes] M WITH (NOLOCK)
    WHERE M.MoStatusMovimentacao = 0
      AND M.MoCampanhasID NOT IN (4, 6, 8, 19, 23, 27, 34, 36, 42, 46, 47, 54, 59, 63, 67, 73, 85, 97, 99, 100, 102, 103, 80, 96, 60, 38, 44, 65, 64, 49)
      AND M.MoNumeroDocumento NOT LIKE 'AC%'
      AND NOT EXISTS (
            SELECT 1
            FROM [Candiotto_STD].[dbo].[Movimentacoes] AC WITH (NOLOCK)
            WHERE AC.MoInadimplentesID = M.MoInadimplentesID
              AND AC.MoCampanhasID     = M.MoCampanhasID
              AND AC.MoNumeroDocumento LIKE 'AC%'
      )
      AND NOT EXISTS (
            SELECT 1
            FROM [Candiotto_reports].[dbo].[AcoesDigitaisLog] ADL WITH (NOLOCK)
            WHERE ADL.ClienteID = M.MoInadimplentesID
              AND ADL.DataEnvio >= DATEADD(DAY, -10, GETDATE())
      )
      AND EXISTS (
            SELECT 1
            FROM [Candiotto_STD].[dbo].[PessoasContatos] PC WITH (NOLOCK)
            WHERE PC.PesPessoasID = M.MoInadimplentesID
              AND ISNULL(PC.PesTelefone, '') <> ''
      )
),
Clientes AS (
    SELECT
        d.PessoaID,
        CASE WHEN EXISTS (
                 SELECT 1
                 FROM [Candiotto_STD].[dbo].[PessoasContatos] PC WITH (NOLOCK)
                 WHERE PC.PesPessoasID = d.PessoaID
                   AND PC.PesTelefonePrincipal = 1
                   AND ISNULL(PC.PesTelefone, '') <> ''
             ) THEN 1 ELSE 2 END AS TierTelefone
    FROM (SELECT DISTINCT PessoaID FROM Base) d
),
Amostra AS (
    SELECT TOP (2000) PessoaID, TierTelefone
    FROM Clientes
    ORDER BY TierTelefone ASC, NEWID()
)
SELECT
    b.PessoaID                                             AS [pessoaId],
    em.Email                                               AS [email],
    tel.Telefone                                           AS [telefone],
    Candiotto_STD.dbo.RetornaNomeRazaoSocial(b.CredorID)   AS [credor],
    Candiotto_STD.dbo.RetornaNomeCampanha(b.CampanhaID, 1) AS [campanha],
    Candiotto_STD.dbo.RetornaNomeRazaoSocial(b.PessoaID)   AS [nome]
FROM Base b
INNER JOIN Amostra a
    ON a.PessoaID = b.PessoaID
OUTER APPLY (
    SELECT TOP (1) PC.PesEmail AS Email
    FROM [Candiotto_STD].[dbo].[PessoasContatos] PC WITH (NOLOCK)
    WHERE PC.PesPessoasID = b.PessoaID
      AND ISNULL(PC.PesEmail, '') <> ''
    ORDER BY PC.PesEmail
) em
OUTER APPLY (
    SELECT TOP (1) PC.PesDDD + PC.PesTelefone AS Telefone
    FROM [Candiotto_STD].[dbo].[PessoasContatos] PC WITH (NOLOCK)
    WHERE PC.PesPessoasID = b.PessoaID
      AND ISNULL(PC.PesTelefone, '') <> ''
    ORDER BY PC.PesTelefonePrincipal DESC
) tel
ORDER BY [campanha], [nome];
