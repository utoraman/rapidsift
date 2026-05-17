import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(config: dict, subject: str, body: str):
    email_cfg = config["email"]
    if not email_cfg["enabled"]:
        print(f"[EMAIL DISABLED] {subject}: {body}")
        return

    msg = MIMEMultipart()
    msg["From"] = email_cfg["from_addr"]
    msg["To"] = email_cfg["to_addr"]
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
        server.starttls()
        server.login(email_cfg["username"], email_cfg["password"])
        server.send_message(msg)


def notify_signals(config: dict, signals: list[dict]):
    if not signals:
        return

    subject = f"Market Alert: {len(signals)} signal(s) fired"
    rows = ""
    for sig in signals:
        color = "#28a745" if sig["direction"] == "buy" else "#dc3545" if sig["direction"] == "sell" else "#ffc107"
        rows += f"""
        <tr>
            <td>{sig['ticker']}</td>
            <td style="color:{color};font-weight:bold">{sig['direction'].upper()}</td>
            <td>{sig['type']}</td>
            <td>{sig['detail']}</td>
        </tr>"""

    body = f"""
    <html><body>
    <h2>Market Monitor Alerts</h2>
    <table border="1" cellpadding="8" cellspacing="0">
        <tr><th>Ticker</th><th>Signal</th><th>Type</th><th>Detail</th></tr>
        {rows}
    </table>
    </body></html>
    """
    send_email(config, subject, body)
