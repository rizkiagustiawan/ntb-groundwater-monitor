import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == "NTB Groundwater Monitoring"
    assert "legal_basis" in data


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    """Integration test — requires running DB (docker-compose)."""
    resp = await client.get("/health")
    if resp.status_code == 503:
        pytest.skip("DB not available — run with docker-compose for integration tests")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
