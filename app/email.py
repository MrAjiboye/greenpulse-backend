"""
Email sending utilities — uses the Resend SDK.
RESEND_API_KEY must be set in .env.
Raises RuntimeError if the key is missing.
"""

import logging
import resend

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, html_body: str, plain_body: str = "") -> None:
    """Send an email via Resend. Raises RuntimeError if API key is not configured."""
    if not settings.RESEND_API_KEY:
        raise RuntimeError(
            "Resend is not configured. Set RESEND_API_KEY in .env"
        )

    resend.api_key = settings.RESEND_API_KEY

    params: resend.Emails.SendParams = {
        "from": f"{settings.FROM_NAME} <{settings.FROM_EMAIL}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    if plain_body:
        params["text"] = plain_body

    resend.Emails.send(params)
    logger.info("Email sent to %s — %s", to_email, subject)


def send_verification_email(to_email: str, token: str, first_name: str) -> None:
    """Send the account verification email with a signed token link."""
    verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"

    subject = "Verify your GreenPulse account"

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#059669,#10b981);padding:32px 40px;text-align:center;">
              <span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">🌿 GreenPulse</span>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px 40px 32px;">
              <h1 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#111827;">
                Hi {first_name}, please verify your email
              </h1>
              <p style="margin:0 0 24px;font-size:15px;color:#6b7280;line-height:1.6;">
                Thanks for signing up! Click the button below to confirm your email address
                and get access to your GreenPulse dashboard.
              </p>

              <!-- CTA button -->
              <table cellpadding="0" cellspacing="0" style="margin:0 0 32px;">
                <tr>
                  <td style="background:#059669;border-radius:8px;text-align:center;">
                    <a href="{verify_url}"
                       style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;letter-spacing:0.2px;">
                      Verify my email
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:0 0 8px;font-size:13px;color:#9ca3af;">
                This link expires in <strong>24 hours</strong>. If you didn't create an account,
                you can safely ignore this email.
              </p>
              <p style="margin:0;font-size:13px;color:#9ca3af;word-break:break-all;">
                Or copy this URL into your browser:<br>
                <a href="{verify_url}" style="color:#059669;">{verify_url}</a>
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;text-align:center;">
              <p style="margin:0;font-size:12px;color:#9ca3af;">
                © 2026 GreenPulse Inc. · Sustainability analytics for hospitality businesses
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    plain_body = (
        f"Hi {first_name},\n\n"
        "Please verify your GreenPulse account by opening the link below:\n\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours.\n\n"
        "If you didn't create an account, you can ignore this email.\n\n"
        "— The GreenPulse Team"
    )

    send_email(to_email, subject, html_body, plain_body)


def send_alert_email(
    to_email: str,
    first_name: str,
    alert_title: str,
    alert_message: str,
    org_name: str,
    anomaly_count: int,
    dashboard_url: str,
) -> None:
    """Send an energy alert email to a manager when high-severity anomalies are detected."""
    subject = f"⚡ Energy alert: {anomaly_count} anomal{'y' if anomaly_count == 1 else 'ies'} detected — {org_name}"

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#059669,#10b981);padding:32px 40px;text-align:center;">
              <span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">🌿 GreenPulse</span>
            </td>
          </tr>

          <!-- Alert banner -->
          <tr>
            <td style="background:#fef2f2;border-bottom:2px solid #fecaca;padding:16px 40px;">
              <p style="margin:0;font-size:13px;font-weight:700;color:#dc2626;text-transform:uppercase;letter-spacing:0.5px;">
                ⚡ Energy Alert — {org_name}
              </p>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px 40px 32px;">
              <h1 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#111827;">
                Hi {first_name}, action may be required
              </h1>
              <p style="margin:0 0 24px;font-size:15px;color:#6b7280;line-height:1.6;">
                GreenPulse detected <strong style="color:#111827;">{anomaly_count} high-severity energy anomal{'y' if anomaly_count == 1 else 'ies'}</strong>
                in your facility in the last 7 days. This may indicate equipment left running, a fault, or an unexpected usage event.
              </p>

              <!-- Alert detail box -->
              <table cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 28px;">
                <tr>
                  <td style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;">
                    <p style="margin:0;font-size:14px;color:#374151;line-height:1.6;">{alert_message}</p>
                  </td>
                </tr>
              </table>

              <!-- CTA button -->
              <table cellpadding="0" cellspacing="0" style="margin:0 0 32px;">
                <tr>
                  <td style="background:#dc2626;border-radius:8px;text-align:center;">
                    <a href="{dashboard_url}"
                       style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;letter-spacing:0.2px;">
                      View alerts on dashboard →
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:0;font-size:13px;color:#9ca3af;line-height:1.6;">
                Log in to your GreenPulse dashboard to review zone-level consumption and take action.
                If this looks expected, you can dismiss the alert from the Notifications page.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;text-align:center;">
              <p style="margin:0;font-size:12px;color:#9ca3af;">
                © 2026 GreenPulse Inc. · You're receiving this because you are a manager on <strong>{org_name}</strong>.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    plain_body = (
        f"Hi {first_name},\n\n"
        f"GreenPulse detected {anomaly_count} high-severity energy anomal{'y' if anomaly_count == 1 else 'ies'} "
        f"in {org_name} in the last 7 days.\n\n"
        f"{alert_message}\n\n"
        f"View your alerts: {dashboard_url}\n\n"
        "Log in to review zone-level consumption and take action.\n\n"
        "— The GreenPulse Team"
    )

    send_email(to_email, subject, html_body, plain_body)


def send_invite_email(to_email: str, token: str, org_name: str, inviter_name: str) -> None:
    """Send a team invite email with a signed token link."""
    invite_url = f"{settings.FRONTEND_URL}/accept-invite?token={token}"

    subject = f"You've been invited to join {org_name} on GreenPulse"

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#059669,#10b981);padding:32px 40px;text-align:center;">
              <span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">🌿 GreenPulse</span>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px 40px 32px;">
              <h1 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#111827;">
                You've been invited to join {org_name}
              </h1>
              <p style="margin:0 0 24px;font-size:15px;color:#6b7280;line-height:1.6;">
                <strong>{inviter_name}</strong> has invited you to join <strong>{org_name}</strong>
                on GreenPulse — a sustainability analytics platform.
                Click below to create your account and get started.
              </p>

              <!-- CTA button -->
              <table cellpadding="0" cellspacing="0" style="margin:0 0 32px;">
                <tr>
                  <td style="background:#059669;border-radius:8px;text-align:center;">
                    <a href="{invite_url}"
                       style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;letter-spacing:0.2px;">
                      Accept invitation
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:0 0 8px;font-size:13px;color:#9ca3af;">
                This invitation expires in <strong>7 days</strong>. If you weren't expecting this,
                you can safely ignore this email.
              </p>
              <p style="margin:0;font-size:13px;color:#9ca3af;word-break:break-all;">
                Or copy this URL into your browser:<br>
                <a href="{invite_url}" style="color:#059669;">{invite_url}</a>
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;text-align:center;">
              <p style="margin:0;font-size:12px;color:#9ca3af;">
                © 2026 GreenPulse Inc. · Sustainability analytics for hospitality businesses
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    plain_body = (
        f"You've been invited to join {org_name} on GreenPulse.\n\n"
        f"{inviter_name} has invited you. Accept the invitation here:\n\n"
        f"{invite_url}\n\n"
        "This link expires in 7 days.\n\n"
        "If you weren't expecting this, you can ignore this email.\n\n"
        "— The GreenPulse Team"
    )

    send_email(to_email, subject, html_body, plain_body)
