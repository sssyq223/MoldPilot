from conftest import sign_in


def test_mail_monitor_config_round_trip_and_secret_guard(client):
    sign_in(client)

    response = client.get('/api/mail-monitor/config')
    assert response.status_code == 200, response.text
    assert response.json()['accounts'] == []
    assert response.json()['defaults']['host'] == 'imap.qiye.163.com'

    payload = {
        'name': '供应商企业邮箱',
        'host': 'imap.qiye.163.com',
        'port': 993,
        'username': 'planner@example.com',
        'folder': 'INBOX',
        'transport': 'ssl',
        'secret_ref': 'env://MOLDPILOT_MAIL_PASSWORD',
        'allowed_senders': ['planner@example.com', '@example.com'],
        'keywords': {'weekly': ['齐套', '周齐套']},
        'poll_interval_seconds': 60,
        'lookback_days': 7,
    }
    response = client.put('/api/mail-monitor/config', json=payload)
    assert response.status_code == 200, response.text
    account = response.json()
    assert account['name'] == payload['name']
    assert account['allowed_senders'] == payload['allowed_senders']
    assert account['secret_configured'] is False
    assert 'password' not in account

    response = client.post(f"/api/mail-monitor/config/{account['id']}/start")
    assert response.status_code == 400, response.text
    assert 'MAIL_SECRET_MISSING' in response.text

