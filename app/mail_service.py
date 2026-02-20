"""
Microsoft OAuth2 + IMAP mail fetcher.

Uses refresh_token + client_id to obtain access_token,
then connects to outlook.office365.com via IMAP with XOAUTH2.
"""
import imaplib
import email
import base64
import logging
from email.header import decode_header
from datetime import datetime, timezone
from dataclasses import dataclass, field
from cachetools import TTLCache
import httpx
from bs4 import BeautifulSoup
import bleach

logger = logging.getLogger(__name__)

# Cache: key = outlook_email, value = (access_token, emails_list)
_token_cache: TTLCache = TTLCache(maxsize=500, ttl=3000)  # tokens live ~50 min
_mail_cache: TTLCache = TTLCache(maxsize=500, ttl=120)     # mail cache 2 min


@dataclass
class MailMessage:
    uid: str
    sender: str
    sender_email: str
    subject: str
    date: str
    date_iso: str
    body_html: str
    body_text: str
    is_read: bool = False
    has_attachments: bool = False
    attachments: list = field(default_factory=list)
    folder: str = "INBOX"


def _decode_mime_words(s: str) -> str:
    """Decode MIME encoded words in header fields."""
    if not s:
        return ""
    decoded_parts = []
    for part, charset in decode_header(s):
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(part)
    return " ".join(decoded_parts)


def _generate_xoauth2_string(user: str, access_token: str) -> str:
    """Generate XOAUTH2 authentication string."""
    auth_string = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(auth_string.encode()).decode()


async def get_access_token(refresh_token: str, client_id: str) -> str:
    """
    Exchange refresh_token for a new access_token via Microsoft OAuth2.
    """
    cache_key = f"token_{client_id}_{refresh_token[:20]}"
    if cache_key in _token_cache:
        return _token_cache[cache_key]

    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(token_url, data=data)
        if response.status_code != 200:
            logger.error(f"Token refresh failed: {response.status_code} — {response.text}")
            raise Exception(f"Failed to refresh token: {response.status_code}")
        
        token_data = response.json()
        access_token = token_data["access_token"]
        _token_cache[cache_key] = access_token
        return access_token


def _extract_body(msg: email.message.Message) -> tuple[str, str]:
    """Extract HTML and plain text body from email message."""
    body_html = ""
    body_text = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                continue

            if content_type == "text/html":
                body_html = decoded
            elif content_type == "text/plain":
                body_text = decoded
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    body_html = decoded
                else:
                    body_text = decoded
        except Exception:
            pass

    # Sanitize HTML
    if body_html:
        body_html = bleach.clean(
            body_html,
            tags=["p", "br", "div", "span", "a", "img", "table", "tr", "td", "th",
                  "thead", "tbody", "h1", "h2", "h3", "h4", "h5", "h6",
                  "strong", "b", "em", "i", "u", "ul", "ol", "li", "blockquote",
                  "pre", "code", "hr", "style", "font", "center"],
            attributes={
                "*": ["style", "class", "id", "align", "valign", "width", "height", "bgcolor", "color"],
                "a": ["href", "target", "rel"],
                "img": ["src", "alt", "width", "height"],
                "font": ["color", "size", "face"],
            },
            strip=True,
        )

    if not body_html and body_text:
        body_html = f"<pre style='white-space: pre-wrap; font-family: inherit;'>{body_text}</pre>"

    if not body_text and body_html:
        soup = BeautifulSoup(body_html, "html.parser")
        body_text = soup.get_text(separator="\n", strip=True)

    return body_html, body_text


def _check_attachments(msg: email.message.Message) -> tuple[bool, list]:
    """Check if message has attachments and return their names."""
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                filename = part.get_filename()
                if filename:
                    filename = _decode_mime_words(filename)
                    attachments.append(filename)
    return len(attachments) > 0, attachments


def _parse_email_date(date_str: str) -> tuple[str, str]:
    """Parse email date string into display and ISO formats."""
    if not date_str:
        return "", ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        display = dt.strftime("%b %d, %Y %I:%M %p")
        iso = dt.isoformat()
        return display, iso
    except Exception:
        return date_str, ""


def _parse_sender(from_header: str) -> tuple[str, str]:
    """Parse sender name and email from From header."""
    decoded = _decode_mime_words(from_header or "")
    
    # Try to extract name and email
    if "<" in decoded and ">" in decoded:
        name = decoded[:decoded.index("<")].strip().strip('"').strip("'")
        addr = decoded[decoded.index("<") + 1:decoded.index(">")].strip()
        return name or addr, addr
    
    return decoded, decoded


def _fetch_folder_messages(
    imap: imaplib.IMAP4_SSL,
    folder: str,
    limit: int,
) -> list[MailMessage]:
    """Fetch messages from a single IMAP folder (already authenticated)."""
    messages: list[MailMessage] = []
    try:
        status, _ = imap.select(folder, readonly=True)
        if status != "OK":
            logger.warning(f"Could not select folder {folder}")
            return messages

        status, data = imap.search(None, "ALL")
        if status != "OK" or not data[0]:
            return messages

        mail_ids = data[0].split()
        if not mail_ids:
            return messages

        latest_ids = mail_ids[-limit:]
        latest_ids.reverse()

        for mail_id in latest_ids:
            try:
                status, msg_data = imap.fetch(mail_id, "(RFC822 FLAGS)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                flags_data = msg_data[0][0].decode() if isinstance(msg_data[0][0], bytes) else str(msg_data[0][0])
                is_read = "\\Seen" in flags_data

                sender_name, sender_email_addr = _parse_sender(msg.get("From", ""))
                subject = _decode_mime_words(msg.get("Subject", "(No Subject)"))
                date_display, date_iso = _parse_email_date(msg.get("Date", ""))
                body_html, body_text = _extract_body(msg)
                has_attach, attach_list = _check_attachments(msg)

                uid_str = mail_id.decode() if isinstance(mail_id, bytes) else str(mail_id)

                messages.append(MailMessage(
                    uid=f"{folder}:{uid_str}",
                    sender=sender_name,
                    sender_email=sender_email_addr,
                    subject=subject,
                    date=date_display,
                    date_iso=date_iso,
                    body_html=body_html,
                    body_text=body_text,
                    is_read=is_read,
                    has_attachments=has_attach,
                    attachments=attach_list,
                    folder=folder,
                ))
            except Exception as e:
                logger.warning(f"Failed to parse email {mail_id} in {folder}: {e}")
                continue
    except Exception as e:
        logger.warning(f"Error reading folder {folder}: {e}")

    return messages


# All folders to fetch: INBOX + Junk/Spam
_ALL_FOLDERS = ["INBOX", "Junk"]


async def fetch_emails(
    outlook_email: str,
    refresh_token: str,
    client_id: str,
    folder: str = "ALL",
    limit: int = 50,
) -> list[MailMessage]:
    """
    Fetch emails from ALL folders (INBOX + Junk) in a single IMAP session.
    Results are merged, sorted by date (newest first), and cached.
    """
    cache_key = f"mail_{outlook_email}_ALL_{limit}"
    if cache_key in _mail_cache:
        return _mail_cache[cache_key]

    access_token = await get_access_token(refresh_token, client_id)
    auth_string = _generate_xoauth2_string(outlook_email, access_token)

    all_messages: list[MailMessage] = []

    try:
        imap = imaplib.IMAP4_SSL("outlook.office365.com", 993)
        imap.authenticate("XOAUTH2", lambda x: auth_string.encode())

        # Fetch from each folder
        for fld in _ALL_FOLDERS:
            folder_msgs = _fetch_folder_messages(imap, fld, limit)
            all_messages.extend(folder_msgs)

        try:
            imap.close()
        except Exception:
            pass
        imap.logout()

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error for {outlook_email}: {e}")
        raise Exception(f"Mail connection failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error fetching mail for {outlook_email}: {e}")
        raise

    # Sort all messages by date (newest first)
    all_messages.sort(key=lambda m: m.date_iso or "", reverse=True)

    # Trim to limit
    all_messages = all_messages[:limit]

    _mail_cache[cache_key] = all_messages
    return all_messages


async def fetch_single_email(
    outlook_email: str,
    refresh_token: str,
    client_id: str,
    uid: str,
    folder: str = "INBOX",
) -> MailMessage | None:
    """Fetch a single email by UID. UID format is 'FOLDER:id'."""
    # Parse folder:uid format
    if ":" in uid:
        parts = uid.split(":", 1)
        folder = parts[0]
        raw_uid = parts[1]
    else:
        raw_uid = uid

    # First check cache
    cache_key = f"mail_{outlook_email}_ALL_50"
    if cache_key in _mail_cache:
        for msg in _mail_cache[cache_key]:
            if msg.uid == uid:
                return msg

    access_token = await get_access_token(refresh_token, client_id)
    auth_string = _generate_xoauth2_string(outlook_email, access_token)

    try:
        imap = imaplib.IMAP4_SSL("outlook.office365.com", 993)
        imap.authenticate("XOAUTH2", lambda x: auth_string.encode())
        imap.select(folder, readonly=True)

        fetch_uid = raw_uid.encode() if isinstance(raw_uid, str) else raw_uid
        status, msg_data = imap.fetch(fetch_uid, "(RFC822 FLAGS)")
        if status != "OK" or not msg_data or not msg_data[0]:
            return None

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        flags_data = msg_data[0][0].decode() if isinstance(msg_data[0][0], bytes) else str(msg_data[0][0])
        is_read = "\\Seen" in flags_data

        sender_name, sender_email_addr = _parse_sender(msg.get("From", ""))
        subject = _decode_mime_words(msg.get("Subject", "(No Subject)"))
        date_display, date_iso = _parse_email_date(msg.get("Date", ""))
        body_html, body_text = _extract_body(msg)
        has_attach, attach_list = _check_attachments(msg)

        try:
            imap.close()
        except Exception:
            pass
        imap.logout()

        return MailMessage(
            uid=uid,
            sender=sender_name,
            sender_email=sender_email_addr,
            subject=subject,
            date=date_display,
            date_iso=date_iso,
            body_html=body_html,
            body_text=body_text,
            is_read=is_read,
            has_attachments=has_attach,
            attachments=attach_list,
            folder=folder,
        )
    except Exception as e:
        logger.error(f"Error fetching email {uid}: {e}")
        return None


def invalidate_cache(outlook_email: str):
    """Clear cached data for a specific account."""
    keys_to_remove = [k for k in _mail_cache if k.startswith(f"mail_{outlook_email}_")]
    for k in keys_to_remove:
        del _mail_cache[k]
