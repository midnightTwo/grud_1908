"""
Smart parser for bulk-uploaded Outlook account strings.

Expected format (colon-separated):
  email:password:recovery_email:recovery_password:refresh_token:client_id

We need to extract: outlook_email, refresh_token, client_id
and IGNORE: password, recovery_email, recovery_password
"""
import re
from dataclasses import dataclass


@dataclass
class ParsedAccount:
    outlook_email: str
    refresh_token: str
    client_id: str


def parse_account_line(line: str) -> ParsedAccount | None:
    """
    Parse a single line of account data.
    
    The format is: email:password:recovery_email:recovery_password:refresh_token:client_id
    
    We identify fields by pattern:
    - email: contains @ and looks like an email
    - refresh_token: typically a long JWT-like string (longest non-email, non-GUID field)
    - client_id: UUID/GUID format (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = line.split(":")
    
    # Standard 6-field format
    if len(parts) >= 6:
        email = parts[0].strip()
        # parts[1] = password (ignore)
        # parts[2] = recovery_email (ignore)
        # parts[3] = recovery_password (ignore)
        refresh_token = parts[4].strip()
        client_id = parts[5].strip()
        
        if "@" in email and refresh_token and client_id:
            return ParsedAccount(
                outlook_email=email,
                refresh_token=refresh_token,
                client_id=client_id,
            )
    
    # Fallback: try to identify fields by pattern
    if len(parts) >= 3:
        emails = [p.strip() for p in parts if "@" in p and "." in p]
        # Find UUID-like client_id
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
        client_ids = [p.strip() for p in parts if uuid_pattern.match(p.strip())]
        
        # refresh_token is typically the longest remaining field
        remaining = [p.strip() for p in parts if p.strip() not in emails and p.strip() not in client_ids]
        remaining.sort(key=len, reverse=True)
        
        if emails and client_ids and remaining:
            return ParsedAccount(
                outlook_email=emails[0],
                refresh_token=remaining[0],
                client_id=client_ids[0],
            )
    
    return None


def parse_bulk_accounts(text: str) -> tuple[list[ParsedAccount], list[str]]:
    """
    Parse multiple lines of account data.
    Returns (parsed_accounts, error_lines).
    """
    accounts = []
    errors = []

    for i, line in enumerate(text.strip().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        try:
            account = parse_account_line(line)
            if account:
                accounts.append(account)
            else:
                errors.append(f"Line {i}: Could not parse — {line[:80]}...")
        except Exception as e:
            errors.append(f"Line {i}: Error — {str(e)}")

    return accounts, errors
