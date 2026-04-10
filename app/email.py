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


def send_insight_digest_email(
    to_email: str,
    first_name: str,
    org_name: str,
    insight_count: int,
    total_estimated_savings: float,
    dashboard_url: str,
) -> None:
    """Notify a manager that new AI insights have been generated for their organisation."""
    subject = f"💡 {insight_count} new insight{'s' if insight_count != 1 else ''} for {org_name}"

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

          <!-- Insight banner -->
          <tr>
            <td style="background:#f0fdf4;border-bottom:2px solid #bbf7d0;padding:16px 40px;">
              <p style="margin:0;font-size:13px;font-weight:700;color:#15803d;text-transform:uppercase;letter-spacing:0.5px;">
                💡 New AI Insights — {org_name}
              </p>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px 40px 32px;">
              <h1 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#111827;">
                Hi {first_name}, your AI just found new savings
              </h1>
              <p style="margin:0 0 24px;font-size:15px;color:#6b7280;line-height:1.6;">
                GreenPulse analysed your recent energy data and generated
                <strong style="color:#111827;">{insight_count} new recommendation{'s' if insight_count != 1 else ''}</strong>
                with an estimated combined saving of
                <strong style="color:#059669;">£{total_estimated_savings:,.0f}/month</strong>.
              </p>

              <!-- Savings highlight -->
              <table cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 28px;">
                <tr>
                  <td style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:20px;text-align:center;">
                    <p style="margin:0 0 4px;font-size:32px;font-weight:800;color:#059669;">£{total_estimated_savings:,.0f}</p>
                    <p style="margin:0;font-size:13px;color:#6b7280;">Estimated monthly savings potential</p>
                  </td>
                </tr>
              </table>

              <!-- CTA button -->
              <table cellpadding="0" cellspacing="0" style="margin:0 0 32px;">
                <tr>
                  <td style="background:#059669;border-radius:8px;text-align:center;">
                    <a href="{dashboard_url}"
                       style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;letter-spacing:0.2px;">
                      View insights →
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:0;font-size:13px;color:#9ca3af;line-height:1.6;">
                Log in to review each recommendation and mark them as applied or dismissed.
                Insights are refreshed daily based on your latest usage data.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;text-align:center;">
              <p style="margin:0 0 4px;font-size:12px;color:#9ca3af;">
                © 2026 GreenPulse Inc. · You're receiving this because you're a manager on <strong>{org_name}</strong>.
              </p>
              <p style="margin:0;font-size:12px;color:#9ca3af;">
                Update your notification preferences in <a href="{settings.FRONTEND_URL}/settings" style="color:#059669;">Settings</a>.
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
        f"GreenPulse generated {insight_count} new recommendation{'s' if insight_count != 1 else ''} "
        f"for {org_name} with an estimated saving of £{total_estimated_savings:,.0f}/month.\n\n"
        f"View your insights: {dashboard_url}\n\n"
        "Log in to review and apply them.\n\n"
        "— The GreenPulse Team"
    )

    send_email(to_email, subject, html_body, plain_body)


def send_daily_digest_email(
    to_email: str,
    first_name: str,
    org_name: str,
    total_kwh_7d: float,
    insight_count: int,
    anomaly_count: int,
    estimated_monthly_savings: float,
    dashboard_url: str,
) -> None:
    """Send a daily energy digest email to a manager."""
    subject = f"Your GreenPulse daily update — {org_name}"

    # Status line
    if anomaly_count > 0:
        status_bg = "#fef2f2"
        status_border = "#fecaca"
        status_color = "#dc2626"
        status_label = f"⚡ {anomaly_count} anomal{'y' if anomaly_count == 1 else 'ies'} detected · Review recommended"
    elif insight_count > 0:
        status_bg = "#f0fdf4"
        status_border = "#bbf7d0"
        status_color = "#15803d"
        status_label = f"✅ {insight_count} new insight{'s' if insight_count != 1 else ''} available"
    else:
        status_bg = "#f9fafb"
        status_border = "#e5e7eb"
        status_color = "#6b7280"
        status_label = "✅ All systems normal"

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

          <!-- Status banner -->
          <tr>
            <td style="background:{status_bg};border-bottom:2px solid {status_border};padding:14px 40px;">
              <p style="margin:0;font-size:13px;font-weight:700;color:{status_color};">{status_label}</p>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px 40px 32px;">
              <h1 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#111827;">
                Hi {first_name}, here's your daily update
              </h1>
              <p style="margin:0 0 28px;font-size:15px;color:#6b7280;line-height:1.6;">
                A summary of energy activity for <strong>{org_name}</strong> over the last 7 days.
              </p>

              <!-- Stats grid -->
              <table cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 28px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
                <tr style="background:#f9fafb;">
                  <td style="padding:16px 20px;border-right:1px solid #e5e7eb;width:33%;">
                    <p style="margin:0 0 4px;font-size:22px;font-weight:800;color:#111827;">{total_kwh_7d:,.1f}</p>
                    <p style="margin:0;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">kWh (7 days)</p>
                  </td>
                  <td style="padding:16px 20px;border-right:1px solid #e5e7eb;width:33%;">
                    <p style="margin:0 0 4px;font-size:22px;font-weight:800;color:#059669;">{insight_count}</p>
                    <p style="margin:0;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Active insights</p>
                  </td>
                  <td style="padding:16px 20px;width:34%;">
                    <p style="margin:0 0 4px;font-size:22px;font-weight:800;color:#111827;">£{estimated_monthly_savings:,.0f}</p>
                    <p style="margin:0;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Est. monthly savings</p>
                  </td>
                </tr>
              </table>

              <!-- CTA button -->
              <table cellpadding="0" cellspacing="0" style="margin:0 0 32px;">
                <tr>
                  <td style="background:#059669;border-radius:8px;text-align:center;">
                    <a href="{dashboard_url}"
                       style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;letter-spacing:0.2px;">
                      Open dashboard →
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;text-align:center;">
              <p style="margin:0 0 4px;font-size:12px;color:#9ca3af;">
                © 2026 GreenPulse Inc. · Daily digest for <strong>{org_name}</strong>.
              </p>
              <p style="margin:0;font-size:12px;color:#9ca3af;">
                Change digest frequency in <a href="{settings.FRONTEND_URL}/settings" style="color:#059669;">Settings</a>.
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
        f"Your daily GreenPulse update for {org_name}:\n\n"
        f"  Energy (7 days): {total_kwh_7d:,.1f} kWh\n"
        f"  Active insights: {insight_count}\n"
        f"  Estimated monthly savings: £{estimated_monthly_savings:,.0f}\n"
        f"  Anomalies detected: {anomaly_count}\n\n"
        f"Open your dashboard: {dashboard_url}\n\n"
        "— The GreenPulse Team"
    )

    send_email(to_email, subject, html_body, plain_body)


def send_demo_request_email(
    full_name: str,
    business_name: str,
    email: str,
    phone: str,
    preferred_date: str,
    preferred_time: str,
    message: str = "",
) -> None:
    """Forward a demo booking request to the GreenPulse inbox."""
    DEMO_INBOX = "info@greenpulseanalytics.com"
    subject = f"Demo request - {business_name}"
    msg = message.strip() or "N/A"

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;padding:40px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
        <tr><td style="background:linear-gradient(135deg,#059669,#10b981);padding:32px 40px;text-align:center;">
          <span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">🌿 GreenPulse</span>
        </td></tr>
        <tr><td style="background:#f0fdf4;border-bottom:2px solid #bbf7d0;padding:16px 40px;">
          <p style="margin:0;font-size:13px;font-weight:700;color:#15803d;text-transform:uppercase;letter-spacing:0.5px;">New demo request</p>
        </td></tr>
        <tr><td style="padding:40px;">
          <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
            <tr style="background:#f9fafb;"><td style="padding:12px 16px;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;width:140px;">Full name</td><td style="padding:12px 16px;font-size:14px;color:#111827;">{full_name}</td></tr>
            <tr><td style="padding:12px 16px;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;border-top:1px solid #e5e7eb;">Business</td><td style="padding:12px 16px;font-size:14px;color:#111827;border-top:1px solid #e5e7eb;">{business_name}</td></tr>
            <tr style="background:#f9fafb;"><td style="padding:12px 16px;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;border-top:1px solid #e5e7eb;">Email</td><td style="padding:12px 16px;font-size:14px;border-top:1px solid #e5e7eb;"><a href="mailto:{email}" style="color:#059669;">{email}</a></td></tr>
            <tr><td style="padding:12px 16px;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;border-top:1px solid #e5e7eb;">Phone</td><td style="padding:12px 16px;font-size:14px;color:#111827;border-top:1px solid #e5e7eb;">{phone}</td></tr>
            <tr style="background:#f9fafb;"><td style="padding:12px 16px;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;border-top:1px solid #e5e7eb;">Date</td><td style="padding:12px 16px;font-size:14px;color:#111827;border-top:1px solid #e5e7eb;">{preferred_date}</td></tr>
            <tr><td style="padding:12px 16px;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;border-top:1px solid #e5e7eb;">Time</td><td style="padding:12px 16px;font-size:14px;color:#111827;border-top:1px solid #e5e7eb;">{preferred_time} (GMT)</td></tr>
            <tr style="background:#f9fafb;"><td style="padding:12px 16px;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;border-top:1px solid #e5e7eb;vertical-align:top;">Message</td><td style="padding:12px 16px;font-size:14px;color:#374151;border-top:1px solid #e5e7eb;">{msg}</td></tr>
          </table>
        </td></tr>
        <tr><td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;text-align:center;">
          <p style="margin:0;font-size:12px;color:#9ca3af;">Submitted via greenpulseanalytics.com/demo</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    plain_body = (
        f"New demo request\n\n"
        f"Full name:   {full_name}\n"
        f"Business:    {business_name}\n"
        f"Email:       {email}\n"
        f"Phone:       {phone}\n"
        f"Date:        {preferred_date}\n"
        f"Time:        {preferred_time} (GMT)\n"
        f"Message:     {msg}\n\n"
        "Submitted via greenpulseanalytics.com/demo"
    )

    send_email(DEMO_INBOX, subject, html_body, plain_body)
