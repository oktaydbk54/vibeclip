"""Transactional email via the Resend HTTP API — stdlib only (no new deps).

Used for signup OTP verification. Two modes (EMAIL_MODE env):
  console (default) — print the code to the server log; zero-config, no account.
  resend            — send a real email via Resend (needs RESEND_API_KEY + a
                      verified sending domain set in RESEND_FROM).
Console mode is the self-host default so VibeClip runs with no email provider.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RESEND_ENDPOINT = "https://api.resend.com/emails"
ACCENT = "#9b5cff"


def _mode() -> str:
    """resend only when explicitly asked AND a key exists; else console."""
    mode = os.getenv("EMAIL_MODE", "console").strip().lower()
    if mode == "resend" and os.getenv("RESEND_API_KEY", "").strip():
        return "resend"
    return "console"


def _from_addr() -> str:
    # Must be an address on a domain verified in the Resend dashboard.
    return os.getenv("RESEND_FROM", "VibeClip <noreply@example.com>")


def _otp_html(code: str, name: str = "") -> str:
    hello = f"Hi {name}," if name else "Hi,"
    spaced = " ".join(code)  # visual spacing of the digits
    return f"""\
<!doctype html><html><body style="margin:0;background:#0a0712;
 font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:#e7e2f2;padding:40px 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
   <tr><td align="center">
    <table role="presentation" width="420" cellpadding="0" cellspacing="0"
      style="background:#130d1f;border:1px solid #2a2040;border-radius:14px;
             overflow:hidden">
     <tr><td style="height:3px;background:linear-gradient(90deg,{ACCENT},transparent 60%)"></td></tr>
     <tr><td style="padding:34px 36px 30px">
       <div style="font-weight:800;letter-spacing:.14em;font-size:18px;color:#fff;margin-bottom:4px">
         VIBECLIP</div>
       <div style="font-size:11px;letter-spacing:.3em;color:#615a73;text-transform:uppercase;margin-bottom:26px">
         Verify your email</div>
       <p style="margin:0 0 18px;font-size:15px;color:#c8c2da">{hello}</p>
       <p style="margin:0 0 22px;font-size:15px;color:#918aa6;line-height:1.5">
         Enter this code to finish creating your account:</p>
       <div style="font-family:'Courier New',monospace;font-weight:700;font-size:34px;
                   letter-spacing:.32em;color:{ACCENT};background:#0a0712;
                   border:1px solid #3a2c54;border-radius:9px;padding:18px 0;
                   text-align:center;margin-bottom:22px">{spaced}</div>
       <p style="margin:0;font-size:12.5px;color:#615a73;line-height:1.5">
         This code expires in 10 minutes. If you didn't request it, you can
         safely ignore this email.</p>
     </td></tr>
    </table>
    <div style="font-size:11px;color:#4a4458;margin-top:18px">VibeClip · AI short-form video editor</div>
   </td></tr>
  </table>
</body></html>"""


def send_otp(to_email: str, code: str, name: str = "") -> bool:
    """Send the 6-digit verification code. Returns True on success.

    Dev fallback (no RESEND_API_KEY): prints the code to the console and
    returns True so the signup flow is fully testable without a key.
    """
    subject = f"{code} is your VibeClip verification code"
    text = (f"Your VibeClip verification code is {code}. "
            "It expires in 10 minutes.")

    if _mode() == "console":
        print(f"[emailer] EMAIL_MODE=console — OTP for {to_email}: {code}",
              flush=True)
        return True

    api_key = os.getenv("RESEND_API_KEY", "").strip()

    payload = json.dumps({
        "from": _from_addr(),
        "to": [to_email],
        "subject": subject,
        "html": _otp_html(code, name),
        "text": text,
    }).encode()
    req = urllib.request.Request(
        RESEND_ENDPOINT, data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        print(f"[emailer] Resend HTTP {e.code}: {detail}", flush=True)
        return False
    except Exception as e:  # noqa: BLE001 — never let email failure crash signup
        print(f"[emailer] Resend request failed: {e}", flush=True)
        return False
