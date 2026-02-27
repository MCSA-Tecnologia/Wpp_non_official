import pyodbc
import requests
from fastapi import FastAPI, HTTPException, Request, Header, Depends, status, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from colorama import init, Fore, Style
from typing import Any, Dict, List, Optional
import pyodbc
import pandas as pd
import os
import json
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import settings

auth_key = settings.AUTH_KEY_GENERAL
head_key = settings.HEADER_KEY
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class UnoffWpp(BaseModel):
    telefone: str = ''
    cpf_cnpj: str = ''
    time:str = ''

description = """ 
API do Chatbot de autoatendimento da MCSA.
"""
tags_metadata = [
    {
        "name": "recupera_cpf_cnpj",
        "description": "FUNÇÃO OBSOLETA. Utiliza o cpf/cnpj de um cliente para recuperar informações necessárias para lançar um protocolo no maxsmart, (id do cliente, nome do negociador, id do negociador, email do negociador). Era utilizada quando o BOT diretamente lançava os protocolos.",
    },
]
app = FastAPI(
    title="Chatbot MCSA",
    description=description,
    version="0.1.0",
    contact={
        "name": "MCSA",
        "url": "https://mcsarc.com.br/",
        "email": "guilherme.ribeiro@mcsarc.com.br"
    },
    openapi_tags=tags_metadata,
)


def verify_token(token: str = Depends(oauth2_scheme)):
    """Função de segurança: as chamadas de API precisam ser validadas com o 'auth token'"""
    if token != auth_key:
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")
    return token

def verify_token_dual(token: str = Depends(oauth2_scheme)):
    """Função de segurança: as chamadas de API precisam ser validadas com o 'auth token'. Nesse caso 2 tokens poderiam ser aceitos"""
    if (token != settings.AUTH_KEY_HYPER) and (token != settings.AUTH_KEY_GENERAL):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")
    return token

@app.post("/UN_injectionSheets", tags=["recupera_cpf_cnpj"])
def injection_sheets(req:UnoffWpp, request: Request,
                        headkey: str = Header(..., alias="headkey"),
                        token: str = Depends(verify_token)
                        ):
    """Unofficial Whatsapp
        Adds the successful hyperflow open protocol to sheets
        Args: class UnoffWpp

        Returns: None
    """
    if headkey != head_key:
        raise HTTPException(status_code=403, detail="Invalid Header Match")
    monoid='not found'


@app.post("/UN_injectionSheets", tags=["injectionSheets"])
def injection_sheets(req:UnoffWpp, request: Request,
                        headkey: str = Header(..., alias="headkey"),
                        token: str = Depends(verify_token)
                        ):
    """Unofficial Whatsapp
        Adds the successful hyperflow open protocol to sheets
        Args: class UnoffWpp

        Returns: None
    """
    if headkey != head_key:
        raise HTTPException(status_code=403, detail="Invalid Header Match")
    monoid='not found'

    query="""
    select distinct
    	M.MoInadimplentesID
    from Movimentacoes M
    	inner join Pessoas P on P.Pessoas_ID = M.MoInadimplentesID
    where
    	P.FPesCPF = '02652033093'
    	or
    	P.JPesCNPJ = '02652033093'"""

    try:
        query_negociador = query
        conn = pyodbc.connect(
            'DRIVER={SQL Server};SERVER=' + settings.SERVER_OLD + ';DATABASE=' + settings.DATABASE_OLD + ';UID=' + settings.USERNAME_OLD + ';PWD=' + settings.PASSWORD_OLD
        )
        querr = pd.read_sql_query(query_negociador, conn)

        conn.close()
        print(querr['MoInadimplentesID'].iloc[0])
        monoid=querr['MoInadimplentesID'].iloc[0]

    except Exception as e:
        print(f"Erro ao buscar dados do negociador: {e}")

    write_hf_protocol_by_keys(
     value=f"https://conversas.hyperflow.global/all-chats/{req.telefone}",
     cpf_cnpj=req.cpf_cnpj,
     data_registro=req.time,
     MoInadID=str(monoid)
    )


@app.post("/UN_request_neg", tags=["UN_request_neg"])
def query_email(req: UnoffWpp, request: Request,
              headkey: str = Header(..., alias="headkey"),
              token: str = Depends(verify_token)
              ):
    """Unofficial Whatsapp
        Gets Negociador email by clients CPF/CNPJ
        Args: class UnoffWpp

        Returns: str: negociador email
    """
    if headkey != head_key:
        raise HTTPException(status_code=403, detail="Invalid Header Match")

    try:
        query_negociador = settings.QUERY_NEGOCIADOR_BY_CPF.format(CPF_CNPJ=req.cpf_cnpj)
        conn = pyodbc.connect(
            'DRIVER={SQL Server};SERVER=' + settings.SERVER_OLD + ';DATABASE=' + settings.DATABASE_OLD + ';UID=' + settings.USERNAME_OLD + ';PWD=' + settings.PASSWORD_OLD
        )
        querr = pd.read_sql_query(query_negociador, conn)

        conn.close()
        print(querr['EMAIL'].iloc[0])
    except Exception as e:
        print(f"Erro ao buscar dados do negociador: {e}")
        return f"Erro ao buscar dados do negociador: {e}"
    return querr['EMAIL'].iloc[0]

def write_hf_protocol_by_keys(
    *,
    value: str,
    MoInadID: str,
    cpf_cnpj: str,
    data_registro: str,
    spreadsheet_id: str = "1aeM9KBSpkO37yEkxwn9X506Xlm_eHGavdl4bfnjY_xc",
    token_path: str = "sheetstoken.json",
    sheet_title: Optional[str] = None,
    gid: int = 0,
) -> dict:
    """
    Finds the row where:
      - Column B ('CPF/CNPJ') == cpf_cnpj
      - Column D ('Data Registro') == data_registro

    Then writes:
      - MoInadID into Column E
      - value into Column F

    Parameters
    ----------
    value : str
        Previous value that used to be written to column E; now written to column F.
    MoInadID : str
        New value to write to column E.
    cpf_cnpj : str
        Target identifier to match in column B.
    data_registro : str
        Target date to match in column D.
    spreadsheet_id : str
        Spreadsheet ID.
    token_path : str
        Path to OAuth token JSON.
    sheet_title : str | None
        Sheet tab name. Resolved automatically from gid if not provided.
    gid : int
        Sheet tab gid from URL.

    Returns
    -------
    dict : Google Sheets API update response.
    """
    # Load + refresh credentials
    creds = Credentials.from_authorized_user_file(token_path, scopes=SHEETS_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("Token invalid and cannot refresh. Re-auth required.")

    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # Resolve sheet title if needed
    if sheet_title is None:
        meta = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields="sheets(properties(sheetId,title))",
            )
            .execute()
        )
        for sh in meta.get("sheets", []):
            props = sh.get("properties", {})
            if int(props.get("sheetId", -1)) == int(gid):
                sheet_title = props["title"]
                break
        if sheet_title is None:
            raise ValueError(f"No sheet found with gid={gid}")

    # Fetch only columns B:D (minimal read)
    read_range = f"{sheet_title}!B:D"
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=read_range)
        .execute()
    )

    rows = resp.get("values", [])
    if not rows:
        raise ValueError("Sheet has no data in columns B:D")

    # Locate matching row (1-based indexing in Sheets)
    target_row = None
    for idx, r in enumerate(rows, start=1):
        col_b = r[0].strip() if len(r) > 0 else ""
        col_d = r[2].strip() if len(r) > 2 else ""

        if col_b == cpf_cnpj and col_d == data_registro:
            target_row = idx
            break

    if target_row is None:
        raise ValueError(
            f"No row found where CPF/CNPJ='{cpf_cnpj}' and Data Registro='{data_registro}'"
        )

    # Write MoInadID into E and value into F on the matched row
    write_range = f"{sheet_title}!E{target_row}:F{target_row}"
    body = {"values": [[MoInadID, value]]}

    return (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=write_range,
            valueInputOption="USER_ENTERED",
            body=body,
        )
        .execute()
    )


QUERY_NEGOCIADOR_BY_CPF = """
select distinct
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
order by 5 asc, 4 desc
"""

try:
    query_negociador = QUERY_NEGOCIADOR_BY_CPF
    conn = pyodbc.connect(
        'DRIVER={SQL Server};SERVER=' + settings.SERVER_OLD + ';DATABASE=' + settings.DATABASE_OLD + ';UID=' + settings.USERNAME_OLD + ';PWD=' + settings.PASSWORD_OLD
    )
    querr = pd.read_sql_query(query_negociador, conn)

    conn.close()
except Exception as e:
    print(f"Erro ao buscar dados do negociador: {e}")

def df_to_contacts_json(df: pd.DataFrame, output_path: str = "contacts.json") -> str:
    """
    Create contacts.json from a DataFrame.

    Expects a column named 'Telefone' (Brazil phone numbers).
    Writes an array of dicts, one per row, matching the provided template.

    Returns the output file path.
    """
    if "Telefone" not in df.columns:
        raise ValueError("DataFrame must contain a 'Telefone' column.")

    message = (
        "SABOOORRRR… SEM DÍVIDA!\n"
        "Imagina o gosto de ver tudo resolvido…🤔\n"
        " \n"
        "Aqui na Construtora Tenda você pode incluir as parcelas vencidas + a vencer em um só acordo.\n"
        " \n"
        "E pra fechar com chave de ouro:\n"
        "💰 Entrada facilitada de R$150\n"
        " \n"
        "Garanta essa oferta somente essa semana!!"
    )

    def normalize_phone_br(value) -> str:
        # Keep only digits
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            return "+55"  # fallback (still valid string)

        # If it already includes country code 55, keep it; else add it
        if digits.startswith("55"):
            return f"+{digits}"
        return f"+55{digits}"

    contacts = []
    for _, row in df.iterrows():
        contacts.append({
            "phone": normalize_phone_br(row["Telefone"]),
            "message": message,
            "delay": 30000,
            "sent": False,
            "sentBy": None,
            "sentAt": None
        })

    out = Path(output_path)
    out.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)

df_to_contacts_json(querr)
