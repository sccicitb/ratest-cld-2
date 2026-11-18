# Hasil evaluasi pilot sintetis persampahan

Status penutupan: **belum lulus pilot**. Pemeriksaan mekanis lulus pada 10 kasus,
tetapi review transcript menemukan satu kegagalan interpretasi dan satu kekurangan
kelengkapan asumsi. Penerimaan user/browser dan validasi OPD belum dilakukan.

## Bukti dan batas pengujian

- Mulai: 18 November 2026, 09:31 WIB (02:31 UTC).
- Model: `qwen3.8-flash-next-iq3_s`, melalui konfigurasi chat SCCIC yang tersedia.
- Satu eksekusi untuk masing-masing 10 kasus, total 13 turn percakapan.
- Commit dasar: `5943a60`; working tree juga mencakup perbaikan input adapter
  yang belum di-commit. Daftar perubahan tercatat pada laporan mentah.
- Loop/registry/tool simulasi aktual dengan SQLite in-memory. Prasyarat beberapa
  kasus dimuat dari fixture lewat tool, tanpa panggilan model untuk setup itu.
- Setiap turn membuka SQLAlchemy session baru. Ini memeriksa kontinuitas DB,
  bukan HTTP, reload browser, rendering tombol, atau deployment produksi.
- KB/MCP/sandbox/artifact dinonaktifkan pada runner. P08 mencoba pencarian KB dua
  kali dan menerima status disabled; tidak ada bukti layanan KB produksi gagal.
- Review di bawah dilakukan coding assistant terhadap transcript yang sama;
  bukan penilaian independen atau persetujuan ahli persampahan.

[Laporan JSON lengkap](persampahan-evaluation.json) berisi input sintetis, jawaban,
trace status tool, ekspektasi dan snapshot per turn. `manualReview`, UAT user,
dan review OPD tetap pending. Laporan tidak memuat reasoning atau kredensial.

## Hasil per kasus

| ID | Pemeriksaan otomatis | Review assistant | Temuan |
|---|---|---|---|
| P01 | Lulus | Lulus | 50 → 60/75, rumus/satuan/asal nilai dan pilihan acuan tampil |
| P02 | Lulus | Lulus | Klarifikasi 15/17; jawaban kedua menghasilkan 85 |
| P03 | Lulus | Lulus | Acuan add_two dipertahankan, bukan alternatif terakhir |
| P04 | Lulus | Lulus | 12 × 5,5 = 66; baseline tetap 50 |
| P05 | Lulus | Catatan | Meminta satuan/periode; menambahkan ilustrasi numerik kondisional di luar engine |
| P06 | Lulus | Perlu perbaikan | Hasil nol benar; asumsi eksplisit tidak lengkap pada daftar akhir |
| P07 | Lulus | Lulus | Pembagian nol dilaporkan jujur, tanpa hasil manual |
| P08 | Lulus | Catatan | Menolak klaim kausal; penjelasan domain masih perlu review pakar |
| P09 | Lulus | Catatan | Tidak memakai armada pecahan; klarifikasi panjang dan integer belum ditegakkan server |
| P10 | Lulus | **Gagal** | Nilai absolut -2 berubah menjadi pengurangan 2 |

“Lulus otomatis” hanya berarti kondisi angka/state eksplisit terpenuhi; pada kasus
klarifikasi kriterianya terutama snapshot tidak berubah. Itu tidak memastikan
bahwa pertanyaan klarifikasi mempertahankan maksud user. Hasil ini menunjukkan
mengapa jumlah kasus lolos tidak boleh dilaporkan sebagai akurasi umum model.

## Temuan yang perlu ditindaklanjuti

### F01 — Prioritas pertama: penetapan nilai berubah menjadi operasi tambah

P10 meminta: **“jadikan jumlah armada -2 truk”**, dengan minimum tetap 0.
Model memanggil `clarify_simulation(parameterId="fleet", amount=-2)`. Jawaban
server menjadi “Penambahan -2 armada” dan menawarkan baseline 10 + -2 → 8 atau
skenario aktif 12 + -2 → 10. Snapshot hasil belum berubah, tetapi dua opsi itu
menyelesaikan masalah yang berbeda dari permintaan user.

Kriteria perbaikan: minta koreksi nilai jumlah truk atau tolak nilai -2 sesuai
batas tersimpan. Jangan mengubah operasi set menjadi add atau menghapus batas.
Tambahkan evaluasi natural yang memasangkan “jadikan -2” dengan “kurangi 2” agar
perbaikan tidak memblokir pengurangan yang sah. Detail implementasi perlu rencana
terpisah; runtime belum diubah dalam paket pilot ini.

### F02 — Asumsi eksplisit tidak selalu terbawa dalam hasil tersimpan

P06 memberi asumsi kapasitas seragam dan tenaga kerja cukup. Model akhirnya
mengirim definisi tanpa daftar asumsi setelah dua retry format. Tabel benar, tetapi
bagian asumsi menyatakan tidak dicantumkan. Penyaji server mengikuti data yang
tersedia; ia tidak dapat memulihkan konteks yang dihilangkan model.

Kriteria perbaikan: asumsi yang disepakati tetap tercatat dan ditampilkan setelah
retry/revisi. Jangan mengarang asumsi domain untuk mengisi daftar kosong.

### F03 — Format input daftar masih perlu diperhatikan

P06 dan P07 masing-masing mengalami dua error `scenarios: list_type` sebelum
model memperbaiki bentuk input. Pembungkus tunggal yang didukung adapter tidak
menyelesaikan string-versus-array; penolakan tersebut memang sesuai batas scope.
Belum ada bukti pembungkus dipakai pada eksekusi ini. Regresi adapter terpisah
membuktikan kompatibilitas kedua pembungkus, bukan penurunan retry umum SCCIC.

### Catatan penggunaan dan validasi

P05 menyebut ilustrasi 50/60 dan 100/120 ketika meminta satuan; ilustrasi itu bukan
hasil engine atau data kota. P08 juga menyampaikan penjelasan konseptual dan saran
panjang data historis tanpa review domain. P09 meminta enam kelompok klarifikasi
padahal user sudah menyatakan truk utuh. Ringkas pertanyaan penting dan uji beban
pengguna pada UAT; jangan mengubah perilaku hanya untuk meluluskan satu prompt.

## Keputusan dan langkah berikutnya

Paket evaluasi selesai sebagai baseline bukti. **Fitur belum dinyatakan selesai
atau siap dipakai untuk keputusan kebijakan nyata.** Rekomendasi berikutnya adalah
merencanakan perbaikan F01 lebih dahulu, lalu F02, dan mengulang kasus terdampak
serta regresi lain tanpa menghapus hasil gagal ini. User tetap perlu mencoba alur
klik/chat/reload sesuai [panduan pilot](persampahan.md). Review OPD dan data nyata
menjadi syarat terpisah sebelum penerimaan domain.
