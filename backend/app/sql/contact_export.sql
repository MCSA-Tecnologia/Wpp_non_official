SELECT TOP (10000)
    M.MoInadimplentesID AS pessoaId,
    dbo.RetornaNomeRazaoSocial(M.MoInadimplentesID) AS Nome,
    '' AS email,
    PC.PesDDD + PC.PesTelefone AS Telefone,
    '' AS observacao,
    '' AS Credor,
    CAST(M.MoCampanhasID AS varchar(30)) AS Campanha
FROM Candiotto_STD.dbo.Movimentacoes AS M
INNER JOIN Candiotto_STD.dbo.PessoasContatos AS PC
    ON PC.PesPessoasID = M.MoInadimplentesID
WHERE M.MoStatusMovimentacao = 0
  AND M.MoDataVencimento < GETDATE()
  AND (PC.PesTelefoneInativo = 0 OR PC.PesTelefoneInativo IS NULL)
  AND PC.PesTelefone IS NOT NULL
  AND LEN(PC.PesTelefone) = 9
  AND LEFT(PC.PesTelefone, 1) = '9';
