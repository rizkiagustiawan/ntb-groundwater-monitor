from typing import Optional
import asyncpg


async def get_ndvi_period_range(conn: asyncpg.Connection):
    return await conn.fetchrow("""
        SELECT
            MIN(period_date) AS min_period,
            MAX(period_date) AS max_period
        FROM sentinel2_ndvi
    """)


async def get_latest_ndvi_rows(
    conn: asyncpg.Connection,
    ascending: bool = False,
    limit: Optional[int] = None
):
    order = "ASC" if ascending else "DESC"
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    return await conn.fetch(f"""
        WITH ranked AS (
            SELECT
                location, kabupaten, lat, lon, period_date,
                ndvi, ndwi, vegetation_status,
                ROW_NUMBER() OVER (PARTITION BY location ORDER BY period_date DESC) AS rn,
                ROUND(MIN(ndvi) OVER (PARTITION BY location)::numeric, 3) AS min_ndvi,
                ROUND(MAX(ndvi) OVER (PARTITION BY location)::numeric, 3) AS max_ndvi,
                COUNT(*) OVER (PARTITION BY location) AS n_months
            FROM sentinel2_ndvi
        )
        SELECT
            location, kabupaten, lat, lon,
            ROUND(ndvi::numeric, 3) AS latest_ndvi,
            min_ndvi, max_ndvi, n_months,
            period_date AS latest_period,
            vegetation_status
        FROM ranked
        WHERE rn = 1
        ORDER BY latest_ndvi {order}, location
        {limit_clause}
    """)
