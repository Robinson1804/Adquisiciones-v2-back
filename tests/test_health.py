def test_health_ok(client):
    r = client.get("/health")
    # Status code: 200 when DB connected, 503 when degraded
    assert r.status_code in (200, 503)
    body = r.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded")
    assert "database" in body


def test_health_status_shape(client):
    """Verify the response always has the expected JSON keys."""
    r = client.get("/health")
    body = r.json()
    assert set(body.keys()) >= {"status", "database"}
