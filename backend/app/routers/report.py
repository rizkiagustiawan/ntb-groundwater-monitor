import os
import io
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from openai import OpenAI as KimiClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from app.db import get_pool
from app.utils import classify_ndvi
from app.queries import get_latest_ndvi_rows

router = APIRouter(tags=["report"])


@router.get("/report/pdf")
async def generate_pdf_report():
    """Generate laporan PDF monitoring air tanah NTB."""
    kimi_key = os.getenv("KIMI_API_KEY")
    if not kimi_key:
        raise HTTPException(status_code=503, detail="KIMI_API_KEY not configured")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ambil data terbaru TWS & GWS
        gws_rows = await conn.fetch("""
            SELECT t.period_date,
                   ROUND(AVG(t.tws_anomaly)::numeric, 2) AS avg_tws,
                   ROUND(AVG(t.tws_anomaly - COALESCE(s.sms_anomaly, 0))::numeric, 2) AS avg_gws
            FROM grace_tws t
            LEFT JOIN gldas_sms s ON
                EXTRACT(YEAR FROM t.period_date) = s.year AND
                EXTRACT(MONTH FROM t.period_date) = s.month AND
                ABS(t.lat - s.lat) < 0.01 AND
                ABS(t.lon - s.lon) < 0.01
            GROUP BY t.period_date
            ORDER BY t.period_date DESC
            LIMIT 6
        """)
        ndvi_rows = await get_latest_ndvi_rows(conn, ascending=True)
        kab_rows = await conn.fetch("""
            SELECT kabupaten, COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status_level='normal') AS normal,
                   COUNT(*) FILTER (WHERE status_level='waspada') AS waspada,
                   COUNT(*) FILTER (WHERE status_level IN ('kritis','sangat_kritis')) AS kritis
            FROM well_latest_status GROUP BY kabupaten ORDER BY kabupaten
        """)

        # AI interpretation
        kimi = KimiClient(api_key=kimi_key, base_url="https://api.moonshot.ai/v1")
        ai_resp = kimi.chat.completions.create(
            model="moonshot-v1-8k",
            messages=[{"role":"user","content":
                f"Buat ringkasan eksekutif kondisi sumber daya air NTB dalam 2 paragraf singkat. "
                f"Data: Anomali simpanan air tanah (GWS) regional terkini {float(gws_rows[0]['avg_gws'])} cm EWH, "
                f"Anomali TWS (Total Water Storage) {float(gws_rows[0]['avg_tws'])} cm EWH. "
                f"{sum(r['kritis'] or 0 for r in kab_rows)} sumur kritis. "
                f"GWS dihitung dengan formula Rodell et al. (2009): GWS = TWS - Soil Moisture. "
                f"Tegaskan bahwa GWS satelit adalah indikator regional, bukan pembacaan langsung muka air sumur. "
                f"Bahasa formal untuk laporan pemerintah. Referensi PP 43/2008."}],
            temperature=0.3
        )
        ai_text = ai_resp.choices[0].message.content

        # Build PDF
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)

        BLUE  = HexColor('#0f4c81')
        GREEN = HexColor('#1D9E75')
        RED   = HexColor('#E24B4A')
        AMBER = HexColor('#BA7517')
        LGRAY = HexColor('#f0f4f8')

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('title', fontSize=16, textColor=white,
                                     fontName='Helvetica-Bold', alignment=TA_CENTER)
        sub_style   = ParagraphStyle('sub', fontSize=9, textColor=white,
                                     fontName='Helvetica', alignment=TA_CENTER)
        h2_style    = ParagraphStyle('h2', fontSize=12, textColor=BLUE,
                                     fontName='Helvetica-Bold', spaceAfter=6)
        body_style  = ParagraphStyle('body', fontSize=9, fontName='Helvetica',
                                     leading=14, spaceAfter=4)
        small_style = ParagraphStyle('small', fontSize=8, textColor=HexColor('#666666'),
                                     fontName='Helvetica', leading=12)

        story = []
        now_str = datetime.now().strftime('%d %B %Y %H:%M WIB')

        # Header block
        header_data = [[
            Paragraph('LAPORAN MONITORING AIR TANAH', title_style),
        ],[
            Paragraph('Nusa Tenggara Barat · NTB Groundwater Monitor', sub_style),
        ],[
            Paragraph(f'PP No. 43/2008 · NASA GRACE/GLDAS · Sentinel-2 MSI · {now_str}', sub_style),
        ]]
        header_tbl = Table(header_data, colWidths=[17*cm])
        header_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), BLUE),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [BLUE]),
        ]))
        story.append(header_tbl)
        story.append(Spacer(1, 0.4*cm))

        # Stats row
        total_w   = sum(r['total'] for r in kab_rows)
        total_k   = sum(r['kritis'] or 0 for r in kab_rows)
        latest_gws = float(gws_rows[0]['avg_gws'])
        ndvi_k    = sum(1 for r in ndvi_rows if float(r['latest_ndvi']) < 0.1)

        stats_data = [
            [Paragraph(f'<b>{total_w}</b><br/>Total Sumur', body_style),
             Paragraph(f'<b><font color="#E24B4A">{total_k}</font></b><br/>Sumur Kritis', body_style),
             Paragraph(f'<b><font color="{"#1D9E75" if latest_gws > 0 else "#E24B4A"}">{latest_gws:+.2f} cm</font></b><br/>GWS (Air Tanah)', body_style),
             Paragraph(f'<b><font color="#E24B4A">{ndvi_k}</font></b><br/>Area NDVI Kritis', body_style)]
        ]
        stats_tbl = Table(stats_data, colWidths=[4.25*cm]*4)
        stats_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), LGRAY),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
            ('ROUNDEDCORNERS', [4]),
        ]))
        story.append(stats_tbl)
        story.append(Spacer(1, 0.4*cm))

        # AI Interpretation
        story.append(Paragraph('Interpretasi AI — Analisis Kondisi Terkini', h2_style))
        for para in ai_text.split('\n\n'):
            if para.strip():
                story.append(Paragraph(para.strip(), body_style))
        story.append(Spacer(1, 0.3*cm))

        # GWS table
        story.append(Paragraph('Anomali Groundwater Storage (GWS) - 6 Bulan Terakhir', h2_style))
        story.append(Paragraph('Formula: GWS = TWS (GRACE) - SMS (Soil Moisture GLDAS). Baseline: 2004-2009 mean.', small_style))
        gws_table_data = [['Periode', 'Anomali GWS (cm EWH)', 'Status']]
        for r in gws_rows:
            gws = float(r['avg_gws'])
            status = 'Surplus' if gws > 2 else 'Normal' if gws > 0 else 'Defisit'
            color = '#1D9E75' if gws > 0 else '#E24B4A'
            gws_table_data.append([
                r['period_date'].strftime('%B %Y'),
                Paragraph(f'<font color="{color}"><b>{gws:+.2f}</b></font>', body_style),
                status
            ])
        gt = Table(gws_table_data, colWidths=[6*cm, 6*cm, 5*cm])
        gt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LGRAY]),
            ('GRID', (0,0), (-1,-1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (1,0), (1,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(gt)
        story.append(Spacer(1, 0.3*cm))

        # Kabupaten table
        story.append(Paragraph('Status Sumur Pantau per Kabupaten', h2_style))
        kab_table_data = [['Kabupaten', 'Total', 'Normal', 'Waspada', 'Kritis', 'Risiko']]
        for r in kab_rows:
            k = r['kritis'] or 0
            risk = 'KRITIS' if k >= 2 else 'WASPADA' if k >= 1 else 'NORMAL'
            rc   = '#E24B4A' if k >= 2 else '#BA7517' if k >= 1 else '#1D9E75'
            kab_table_data.append([
                r['kabupaten'], r['total'], r['normal'] or 0,
                r['waspada'] or 0, k,
                Paragraph(f'<font color="{rc}"><b>{risk}</b></font>', body_style)
            ])
        kt = Table(kab_table_data, colWidths=[5.5*cm,2*cm,2.5*cm,2.5*cm,2*cm,2.5*cm])
        kt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LGRAY]),
            ('GRID', (0,0), (-1,-1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (1,0), (-1,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(kt)
        story.append(Spacer(1, 0.3*cm))

        # NDVI table
        story.append(Paragraph('Kondisi Vegetasi - Snapshot NDVI Sentinel-2 Terbaru', h2_style))
        ndvi_table_data = [['Lokasi', 'Kabupaten', 'NDVI Terbaru', 'Kondisi']]
        for r in ndvi_rows:
            ndvi = float(r['latest_ndvi'])
            kondisi = classify_ndvi(ndvi, title_case=True)
            nc = '#1D9E75' if ndvi >= 0.5 else '#BA7517' if ndvi >= 0.2 else '#E24B4A'
            ndvi_table_data.append([
                r['location'], r['kabupaten'],
                Paragraph(f'<font color="{nc}"><b>{ndvi:.3f}</b></font>', body_style),
                kondisi
            ])
        nt = Table(ndvi_table_data, colWidths=[4.5*cm,5*cm,3.5*cm,4*cm])
        nt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LGRAY]),
            ('GRID', (0,0), (-1,-1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (2,0), (2,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(nt)
        story.append(Spacer(1, 0.4*cm))

        # Legal footer
        legal = ('Dasar Hukum: PP No. 43 Tahun 2008 · Perpres No. 33 Tahun 2018 | '
                 'Metodologi: GWS = TWS (GRACE) - SMS (GLDAS Noah 2.1). Rodell et al. (2009) doi:10.1038/nature08232 | '
                 'Disclaimer: Laporan ini berupa estimasi regional (resolusi ~55 km). Keputusan kebijakan harus dikonfirmasi pengukuran lapangan.')
        story.append(Paragraph(legal, small_style))

        doc.build(story)
        pdf_bytes = buf.getvalue()

        filename = f"laporan-air-tanah-ntb-{datetime.now().strftime('%Y%m%d')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
