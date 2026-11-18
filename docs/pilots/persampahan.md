# Pilot sintetis kapasitas layanan persampahan

Status: paket uji teknis; belum penerimaan OPD atau model dampak kebijakan.

## Tujuan dan keputusan yang didukung

Membandingkan kapasitas angkut teoretis harian beberapa skenario armada, dengan
acuan, satuan, asumsi, serta hasil yang dapat diperiksa ulang. Output membantu
membahas alternatif kapasitas; tidak memilih kebijakan optimal atau membuktikan
penurunan timbulan sampah. Data di seluruh paket ini sintetis dan bukan data kota.

## Definisi model

- Jumlah armada: jumlah truk fisik yang beroperasi; untuk pilot berarti unit utuh,
  bukan FTE, jumlah ritasi atau jam-truk. Pecahan perlu diklarifikasi, bukan dibulatkan.
- Kapasitas efektif tiap armada: ton per armada **per hari**, bukan ton per ritasi.
  Angka contoh 5 sudah menyatakan total harian; jangan mengalikannya lagi dengan
  ritasi tanpa mendefinisikan ulang parameter dan satuan.
- Rumus: jumlah armada × kapasitas efektif per armada per hari = ton per hari.
- Baseline: 10 armada × 5 = 50 ton/hari; alternatif 12 → 60 (+10; 20%),
  15 → 75 (+25; 50%). Nilai itu hanya hasil acuan sintetis.
- Asumsi: kapasitas seragam, tenaga kerja cukup, semua armada dapat beroperasi,
  fasilitas penerima tidak membatasi. Asumsi ini harus ditampilkan/disepakati.
- Belum memodelkan rute, perjalanan, pemeliharaan, permintaan, fasilitas, biaya,
  ketidakpastian atau perubahan timbulan. Kapasitas bukan volume aktual terangkut.
- Batas 100 armada pada fixture merupakan pagar contoh pengujian, bukan kebijakan
  operasional atau kapasitas kota yang telah disahkan.

Fixture tetap [fleet.json](../../backend/examples/simulation/fleet.json). Tidak
ada engine atau template runtime baru khusus persampahan. Validator generik
belum memaksa parameter jumlah truk menjadi integer; kasus P09 menilai perilaku
klarifikasi model, bukan bukti bahwa aturan domain sudah ditegakkan server.

## Kasus penerimaan

Sumber percakapan dan kriteria mekanis ada di
[policy_pilot_cases.json](../../backend/examples/simulation/policy_pilot_cases.json).

| ID | Fokus | Bukti yang dicari |
|---|---|---|
| P01 | Baseline dan alternatif | 50 → 60/75, satuan/asal nilai, tanpa pertanyaan acuan |
| P02 | Acuan ambigu + jawaban natural | Opsi 15/17, jawaban kedua → 85; baseline tetap |
| P03 | Memilih alternatif | Pilihan pertama 12 dipertahankan meski 15 ada di akhir |
| P04 | Kenaikan kapasitas eksplisit | 12 × 5,5 = 66, baseline tetap 50 |
| P05 | Satuan/periode belum jelas | Tidak menghitung; tanya ton/ritasi atau ton/hari |
| P06 | Baseline nol | Hasil 10; persentase tidak terdefinisi |
| P07 | Pembagian nol | Klarifikasi atau kegagalan yang jujur; tanpa hasil manual |
| P08 | Klaim dampak kausal | Tidak menyamakan kapasitas dengan penurunan timbulan |
| P09 | Armada pecahan | Klarifikasi jumlah truk utuh; tidak membulatkan sendiri |
| P10 | Armada negatif | Menolak/meminta koreksi; tidak menghapus batas minimum |

## Cara menjalankan evaluasi bahasa natural

Dari `backend`, dengan environment runtime dan konfigurasi SCCIC yang sudah ada:

```bash
PYTHONPATH=. .venv/bin/python scripts/evaluate_policy_pilot.py
```

Perintah tanpa `--live` hanya menampilkan kasus yang dipilih, tanpa panggilan
jaringan atau evaluasi model. Untuk menjalankan semua kasus:

```bash
PYTHONPATH=. .venv/bin/python scripts/evaluate_policy_pilot.py --live --output /tmp/citya-policy-pilot.json
```

Satu kasus dapat dipilih dengan `--case P02`, beberapa dengan mengulang `--case`.
Timeout default 90 detik **per turn**, dapat diubah melalui `--timeout`.
Runner selalu mengisolasi DATABASE_URL sebelum mengimpor aplikasi; SQLite yang
benar-benar dipakai juga in-memory. Ia memakai loop, registry dan tool simulasi
asli. Setiap turn membuka ulang SQLAlchemy session agar konteks tidak bergantung
pada objek ToolContext lama. Ini bukan pengujian reload browser/HTTP.

Kasus berawalan `seedScenarioIds` memuat prasyarat lewat tool aktual dari fixture,
bukan meminta model membangunnya. Untuk UAT browser, jalankan prompt baseline
lebih dahulu; lanjutkan sesuai acuan eksplisit atau jawab pilihan hanya jika diminta. Kasus tanpa seed dimulai
kosong. MCP, KB, sandbox dan artifact sengaja tidak dijalankan: jika diminta,
runner mencatat tool tersebut sebagai dinonaktifkan. Batas ini dapat memengaruhi
hasil dan harus dicatat ketika membandingkan dengan aplikasi lengkap.

Laporan JSON mencatat model, commit, status working tree, percakapan sintetis,
status tool, snapshot dan hasil pemeriksaan; tidak menyimpan reasoning atau
kredensial. Keluar dengan kode 1 berarti ada pemeriksaan gagal/error; kode 0
hanya berarti semua pemeriksaan mekanis lolos. Bukan tanda pilot telah diterima.

## Penilaian dan kriteria penutupan

1. Pemeriksaan otomatis: turn selesai, pesan tersimpan, output/baseline sesuai
   acuan, pilihan/ID aktif benar, snapshot tidak berubah ketika tidak seharusnya.
   Tidak ada penilaian LLM terhadap dirinya sendiri sebagai penentu kelulusan.
2. Review jawaban: baca tiap transcript untuk memastikan satuan/asal nilai benar,
   pertanyaan tepat sasaran, tidak ada asumsi diam-diam atau klaim sebab-akibat.
   Status `manualReview` tetap pending sampai penilai membubuhkan keputusan.
3. UAT browser: user mencoba klik, chat natural, reload dan melanjutkan percakapan.
   Test regresi HTTP/state yang sudah ada dipakai ulang; hasil runner bukan bukti
   bahwa tombol terlihat atau aksesibilitas UI lolos.
4. Review OPD: pemilik program menyetujui definisi variabel, data baseline,
   batasan dan interpretasi. Bila memakai data nyata, verifikasi sumber, periode,
   cakupan, satuan dan izin akses. Review ini belum dilakukan dalam paket sintetis.

Semua kasus wajib lolos pemeriksaan relevan serta review jawaban untuk menutup
pilot teknis; kegagalan dicatat per kasus dan tidak disembunyikan lewat retry
selektif. Ulang seluruh kasus terdampak setelah perbaikan. Satu eksekusi bukan
estimasi akurasi umum model. Pilot kebijakan nyata memerlukan UAT dan review OPD.

## Cakupan test yang dipakai ulang

- Schema/engine: numerik, batas, rumus, persentase nol dan hasil acuan.
- Tool/chat: format input, retry, status gagal, penyaji hasil dari engine.
- Klarifikasi: ID aktif, urutan, pilihan kedaluwarsa, ownership dan reload DB/HTTP.
- Tambahan baru hanya menguji rubric evaluator dengan kontrol negatif; tidak
  menyalin test aritmetika yang sudah tersedia.

Panduan teknis lengkap: [README simulasi](../../backend/app/simulation/README.md).

## Perubahan alur corpus versi 2

Perbandingan beberapa alternatif tidak lagi otomatis membuat pertanyaan acuan.
P01 mengharapkan pending kosong; P03 menguji kelanjutan dengan acuan eksplisit
yang langsung dihitung. P02 tetap menguji penambahan ambigu melalui pilihan.
Laporan evaluasi pertama disimpan sebagai bukti versi 1, bukan hasil pengujian
ulang versi ini. Perubahan tampilan dan kontrak klarifikasi perlu diuji ulang
melalui model nyata serta browser sebelum penerimaan pilot diperbarui.

[Evaluasi tiga alur lanjutan versi 2](persampahan-followup-v2-results.md) sudah
memeriksa P01–P03 melalui SCCIC. UAT browser dan penerimaan pilot lengkap tetap
belum selesai.
