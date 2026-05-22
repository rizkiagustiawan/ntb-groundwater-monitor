# Transparency & Methodology

## Groundwater Estimation Method

Platform ini menggunakan pendekatan neraca air daratan untuk mengestimasi anomali Groundwater Storage (GWS). Karena satelit GRACE mengukur total simpanan air (TWS) yang mencakup air tanah, kelembaban tanah, air permukaan, salju, dan tajuk vegetasi, maka komponen non-air tanah harus dikurangi.

### Formula
Mengikuti metodologi **Rodell et al. (2009)**:

$$GWS_{anomaly} = TWS_{anomaly} (GRACE) - SMS_{anomaly} (GLDAS) - SWS_{anomaly}$$

Untuk wilayah Nusa Tenggara Barat (NTB):
- **SWS (Surface Water Storage):** Diasumsikan 0 untuk estimasi regional skala kasar, namun menjadi faktor ketidakpastian di area bendungan besar.
- **SWE (Snow Water Equivalent):** 0 (wilayah tropis).
- **CWS (Canopy Water Storage):** Negligible/diabaikan.

### Datasets
1.  **NASA GRACE/GRACE-FO JPL Mascon (RL06.3Mv04 CRI):**
    - Mengukur anomali gravitasi bumi yang dikonversi menjadi unit *Liquid Water Equivalent Thickness* (cm).
    - Resolusi asli ~300 km, diproses dalam grid 0.5° (~55 km).
2.  **NASA GLDAS Noah 2.1 (L4 Monthly):**
    - Model asimilasi data permukaan tanah.
    - Digunakan untuk mengambil data *Root Zone Soil Moisture* (0-200 cm).
    - Variabel: `SoilMoi0_10cm_inst`, `SoilMoi10_40cm_inst`, `SoilMoi40_100cm_inst`, `SoilMoi100_200cm_inst`.

### Baseline Period
Seluruh anomali dihitung relatif terhadap rata-rata periode **2004-2009**. Nilai positif menunjukkan surplus simpanan air dibanding rata-rata historis, nilai negatif menunjukkan defisit.

### Limitations & Uncertainty
- **Resolusi Spasial & Signal Leakage:** Data GRACE sangat kasar (~300 km footprint asli). Menggunakan algoritma *Coastline Resolution Improvement* (CRI) membantu meminimalisasi kebocoran sinyal laut ke daratan pulau sempit seperti NTB, namun resolusi tetap bersifat regional, bukan lokal.
- **Hukum Perambatan Ralat (Error Propagation):** Sesuai hukum fisika/statistik, mengurangkan dua variabel ($TWS - SMS$) yang masing-masing memiliki ketidakpastian tidak akan mengurangi *error*, melainkan menambah total ketidakpastian ($\sigma_{GWS} = \sqrt{\sigma_{TWS}^2 + \sigma_{SMS}^2}$). Oleh karena itu, data GWS di platform ini wajib diinterpretasikan sebagai **tren historis regional**, bukan nilai absolut volumetrik.
- **Ketidakpastian Model GLDAS:** Model GLDAS merupakan asimilasi data (bukan observasi satelit langsung) dan memiliki tingkat ketidakpastian estimasi kelembaban tanah sekitar 20-30%, yang dipengaruhi oleh jarangnya stasiun iklim darat.
- **Asumsi Air Permukaan ($SWS = 0$):** Mengabaikan badan air permukaan dapat menyebabkan anomali semu (misalnya volume bendungan baru terbaca sebagai surplus air tanah).
- **Anthropogenic Gap:** Model GLDAS Noah 2.1 **tidak memodelkan** pengambilan air tanah oleh manusia (pumping) atau aktivitas pertambangan (misal: dewatering di Batu Hijau/PT AMNT). Perbedaan antara tren penurunan GWS satelit dan kondisi iklim (CHIRPS) dapat menjadi indikator kuat adanya ekstraksi antropogenik.

### Reference
Rodell, M., Velicogna, I., & Famiglietti, J. S. (2009). Satellite-based estimates of groundwater depletion in India. *Nature*, 461(7266), 997-1000. doi:10.1038/nature08232
