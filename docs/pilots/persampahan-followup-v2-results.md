# Evaluasi klarifikasi lanjutan versi 2

Tiga kasus terpilih diuji melalui model SCCIC `qwen3.8-flash-next-iq3_s` dan
loop/tool aktual. Setiap kasus dieksekusi satu kali, total empat turn, dengan
SQLite terisolasi. Tidak mengubah database aplikasi Citya.

| Kasus | Hasil | Pemeriksaan transcript oleh assistant |
|---|---|---|
| P01 | Lulus | Baseline 50; alternatif 60/75; tanpa pertanyaan acuan setelah perbandingan |
| P02 | Lulus | Tambah lima meminta pilihan 15/17; jawaban “yang kedua” menghasilkan 85 |
| P03 | Lulus | Acuan skenario tambah dua eksplisit langsung menghasilkan 85; tanpa pertanyaan ulang |

[Laporan input, trace tool, jawaban dan state](persampahan-followup-v2-evaluation.json).
Waktu mulai tercatat pada `startedAtUtc`. Laporan tidak memuat API key atau reasoning.

Pengujian ini bukan pengukuran akurasi umum model. Browser host tidak tersedia,
sehingga tampilan, scroll tabel pada mobile dan klik nyata di browser masih perlu
UAT user. Endpoint klik, pending/reload, pemilikan sesi, dan penyimpanan jawaban
diperiksa dengan tes HTTP terisolasi dan model berskrip. Filter tampilan untuk
input warisan yang identik disempurnakan setelah eksekusi live dan diuji otomatis.

Hasil versi 1 tetap tersimpan terpisah. Temuan interpretasi nilai absolut negatif
dan kelengkapan asumsi pada kasus lain belum dinyatakan selesai. Pilot keseluruhan
masih belum lulus; keputusan domain dan UAT pengguna tetap diperlukan.
