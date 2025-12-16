# email_service.py
# Strict SES mailer using config from app-config-<env> via config_loader

import boto3
from botocore.exceptions import ClientError
from typing import List, Dict, Optional, Any
import logging

# Our strict config loader (no table fallbacks)
from config_loader import load_config, get_value, resolved_source, ConfigError  # type: ignore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class EmailError(RuntimeError):
    pass

def _ensure_required_config() -> Dict[str, Any]:
    """
    Load and validate required SES configuration from app-config.
    Raises ConfigError if anything required is missing.
    """
    cfg = load_config()  # merged global + current ENVIRONMENT

    ses_region = get_value("ses_region", required=True)                 # e.g., "us-west-2"
    ses_from_email = get_value("ses_from_email", required=True)         # e.g., "no-reply@juniorbay.com"
    ses_from_name = get_value("ses_from_name", required=True)           # e.g., "Junior Bay Support"

    # Optionals
    ses_reply_to = get_value("ses_reply_to_default", default=None)
    ses_config_set = get_value("ses_configuration_set", default=None)

    return {
        "ses_region": ses_region,
        "ses_from_email": ses_from_email,
        "ses_from_name": ses_from_name,
        "ses_reply_to_default": ses_reply_to,
        "ses_configuration_set": ses_config_set,
    }

def _sender_address(from_name: str, from_email: str) -> str:
    """Format 'Name <email>' safely (SES accepts both raw email and formatted)."""
    from_name = (from_name or "").strip().replace("\n", " ").replace("\r", " ")
    from_email = (from_email or "").strip()
    if not from_email:
        raise ConfigError("ses_from_email is empty after trimming")
    return f'{from_name} <{from_email}>' if from_name else from_email

def _build_ses_client(region: str):
    return boto3.client("sesv2", region_name=region)

def send_email(
    to: List[str],
    subject: str,
    html: str,
    text: Optional[str] = None,
    reply_to: Optional[List[str]] = None,
    tags: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Send an email using Amazon SES v2.
    - Reads SES settings from app-config (strict).
    - `to`: list of recipient emails
    - `subject`: subject line
    - `html`: HTML body
    - `text`: optional text body
    - `reply_to`: optional list of reply-to emails; defaults to ses_reply_to_default if configured
    - `tags`: optional dict of message tags (e.g., {"env": "prod", "tenant": "acme"})
    Returns SES response dict on success; raises EmailError on failure.
    """
    if not to:
        raise EmailError("At least one recipient is required")
    if not subject:
        raise EmailError("Subject is required")
    if not html and not text:
        raise EmailError("At least one of HTML or text body must be provided")

    cfg = _ensure_required_config()
    sender = _sender_address(cfg["ses_from_name"], cfg["ses_from_email"])
    ses = _build_ses_client(cfg["ses_region"])

    # Reply-To handling
    reply_to_addrs: List[str] = []
    if reply_to:
        reply_to_addrs = [addr.strip() for addr in reply_to if addr and addr.strip()]
    elif cfg["ses_reply_to_default"]:
        reply_to_addrs = [cfg["ses_reply_to_default"]]

    # Tags → SES format
    email_tags = [{"Name": k, "Value": v} for k, v in (tags or {}).items()]

    content: Dict[str, Any] = {
        "Simple": {
            "Subject": {"Data": subject},
            "Body": {}
        }
    }
    if html:
        content["Simple"]["Body"]["Html"] = {"Data": html}
    if text:
        content["Simple"]["Body"]["Text"] = {"Data": text}

    params: Dict[str, Any] = {
        "FromEmailAddress": sender,
        "Destination": {"ToAddresses": to},
        "Content": content,
    }
    if reply_to_addrs:
        params["ReplyToAddresses"] = reply_to_addrs
    if cfg["ses_configuration_set"]:
        params["ConfigurationSetName"] = cfg["ses_configuration_set"]
    if email_tags:
        params["EmailTags"] = email_tags

    # Helpful log for diagnostics (no body/PII)
    src = resolved_source()
    logger.info(
        "Sending email via SES: region=%s, from=%s, to=%s, cfg_table=%s env=%s",
        cfg["ses_region"], sender, ",".join(to), src.get("table"), src.get("environment")
    )

    try:
        resp = ses.send_email(**params)
        logger.info("SES send_email success: MessageId=%s", resp.get("MessageId"))
        return resp
    except ClientError as e:
        code = e.response["Error"].get("Code", "Unknown")
        msg = e.response["Error"].get("Message", str(e))
        logger.error("SES send_email failed: %s - %s", code, msg)
        raise EmailError(f"SES error: {code} - {msg}") from e
    except Exception as e:
        logger.exception("Unexpected error from SES send_email")
        raise EmailError(str(e)) from e
