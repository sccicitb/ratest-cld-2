# Structured simulation: engine, chat, dan klarifikasi

Engine lokal untuk membandingkan baseline dengan skenario alternatif. Tool
`run_simulation` menghubungkan engine ke chat dan jawaban Markdown pada UI yang
sudah ada. Snapshot simulasi terbaru dan klarifikasi aktif disimpan per sesi.
Semua angka pada contoh adalah sintetis.

## Mencoba melalui chat

Gunakan environment aplikasi lengkap sesuai `backend/README.md`, termasuk
dependensi runtime embedding/OCR. Environment unit test saja tidak cukup
untuk menjalankan startup aplikasi.
Tool simulasi sendiri tidak memakai Qdrant, LLM, atau sandbox; aplikasi chat tetap
memerlukan environment backend, embedding, dan endpoint LLM dengan tool calling.

Jika setup aplikasi sudah tersedia, jalankan pada terminal terpisah:

```bash
# Dari root repository: infrastruktur RAG yang dipakai aplikasi
docker compose up -d qdrant
```

```bash
# Terminal backend, dari folder backend
COOKIE_SECURE=false .venv/bin/python -m uvicorn app.main:app --port 8000
```

```bash
# Terminal frontend, dari folder frontend; npm ci dahulu jika belum terpasang
npm run dev
```

Buka `http://localhost:5173`, login memakai akun development yang sudah tersedia,
dan buat chat baru. Tidak perlu menjalankan code-exec service untuk tool simulasi.
Contoh prompt pertama:

> Gunakan data sintetis berikut: ada 10 armada, masing-masing berkapasitas 5 ton
> per hari. Asumsikan kapasitas setiap armada sama dan tenaga kerja mencukupi.
> Simulasikan penambahan 2 dan 5 armada. Tampilkan baseline, hasil, selisih,
> persentase, asumsi, dan keterbatasan menggunakan tool run_simulation.

Periksa tracker `Calling tool` untuk `run_simulation`, lalu tabel hasil:
baseline 50, alternatif 60 (+10; +20%) dan 75 (+25; +50%) ton/hari.
Dalam contoh ini nilai absolut override armada adalah 12 dan 15, bukan 2 dan 5.

Contoh klarifikasi (gunakan chat baru):

> Jika anggaran kebersihan dinaikkan 20%, berapa peningkatan kapasitasnya?

Citya diarahkan untuk meminta baseline dan hubungan anggaran-kapasitas atau
menawarkan asumsi untuk disepakati; kenaikan anggaran tidak otomatis berarti
kenaikan kapasitas yang sama. Perilaku klarifikasi mengikuti kualitas model nyata,
bukan jaminan dari validator aritmetika.

Setelah mengganti kode backend, restart proses backend. Jika `SYSTEM_PROMPT`
diisi, prompt tersebut tetap menggantikan default seperti sebelumnya: panduan
simulasi default tidak ikut dimuat, tetapi deskripsi tool tetap dikirim ke model.
Tambahkan panduan yang sesuai pada custom prompt bila diperlukan. Jika jawaban
tidak memanggil tool, periksa konfigurasi endpoint, dukungan tool calling, dan
instruksi prompt; angka dalam teks saja tidak membuktikan engine dijalankan.

Alur: user → model menyusun definisi → `RunSimulation` memvalidasi → engine
menghitung → hasil JSON kembali ke model → jawaban dan event melalui SSE. Tool
memerlukan konteks sesi server, tidak menerima user/session ID dari model.
Maksimum input 32 KiB UTF-8, 32 parameter, 8 output, 8 skenario, dan hasil 128 KiB.
Kegagalan mengembalikan `status: error` beserta kode dan detail yang relevan;
hasil tidak dipotong menjadi JSON parsial.

## Klarifikasi melalui tombol atau chat

Setelah restart backend, migrasi `c82b61f42e30` otomatis menambahkan kolom JSON
nullable `sessions.simulation_state`. Sesi lama tetap valid, tetapi hasil pada
chat lama tidak otomatis dikonversi menjadi snapshot: jalankan simulasi dahulu.

Untuk mencoba acuan yang berbeda secara jelas:

1. Buat chat baru. Kirim: “Gunakan data sintetis: 10 armada, kapasitas masing-masing
   5 ton per hari, kapasitas sama dan tenaga kerja mencukupi. Simulasikan penambahan
   2 armada menggunakan rumus armada dikali kapasitas.” Hasil: baseline 50,
   skenario 60 ton/hari, armada skenario 12.
2. Kirim: “Coba tambah lima armada lagi.” Model diarahkan memanggil
   `clarify_simulation`. Backend membuat opsi baseline 10 + 5 → 15 dan skenario
   terakhir 12 + 5 → 17. Tombol muncul setelah turn selesai.
3. Reload halaman sebelum menjawab: klarifikasi tetap ada. Klik opsi terakhir:
   hasil armada 17, kapasitas 85, selisih 35, perubahan 70% terhadap baseline 50.
4. Ulangi dari chat baru untuk jalur natural. Jawab “yang kedua”: hasil sama.
   Jawab “dari terakhir, tapi tambah tiga”: hasil armada 15, kapasitas 75,
   selisih 25 dan perubahan 50% terhadap baseline. Angka ini memakai skenario
   terakhir 12 dari langkah pertama, bukan hasil dari percobaan klik sebelumnya.
5. “Pilihan lain” memfokuskan composer. Jika jawabannya masih belum menentukan
   acuan, Citya diarahkan bertanya lagi. “Batalkan klarifikasi” dapat diproses
   melalui tool penyelesaian untuk menghapus pending tanpa mengubah hasil.

Alur klik: opsi ID + question ID → validasi pemilik sesi dan pending → registry
turn → backend terapkan penambahan → engine → tabel server. Klik tidak memanggil
LLM. Label dan nilai dari browser tidak dipakai sebagai sumber perhitungan.
Alur natural: snapshot + pesan → model menafsirkan reference/amount →
`resolve_simulation_clarification` → jalur engine yang sama.

`conversation.py` menyimpan snapshot definisi/hasil terakhir, nomor revisi, serta
satu pertanyaan aktif dengan opsi yang mengacu pada revisi tersebut. Hasil baru
mengganti snapshot terakhir; riwayat hasil tetap berupa pesan, belum arsip versi
terstruktur lengkap. Baseline asli dipertahankan pada penambahan lanjutan. Semua
parameter lain mengikuti acuan yang dipilih. Jika ada beberapa alternatif dalam
snapshot, “skenario terakhir” berarti alternatif terakhir dalam daftar hasil tool.

`GET /api/sessions/{id}/simulation` menyediakan pending untuk UI/reload. Klik
mengirim `simulationSelection: {questionId, optionId}` pada endpoint chat yang
sama. Pilihan yang sudah dijawab/kedaluwarsa ditolak. `run_simulation` tidak boleh
menimpa snapshot saat pending; tool code execution juga diblokir dalam turn selama
pending. Pertanyaan dan tabel hasil penyelesaian dibuat server agar model tidak
mengubah angka atau menambahkan perbandingan lain. Narasi pada simulasi awal masih
berasal dari model.

Batas tahap ini: penambahan satu parameter dengan acuan baseline/alternatif
terakhir. Belum mendukung operasi set-total, persen, atau beberapa parameter dalam
satu penyelesaian. Untuk permintaan di luar scope, model diarahkan mengklarifikasi
atau menawarkan pembatalan. Pengenalan ambiguitas dan interpretasi jawaban natural
masih bergantung pada LLM; validasi bukan jaminan semua kalimat dipahami. State
mengikuti batas konkurensi registry turn aplikasi saat ini (satu proses); belum
menggunakan penguncian lintas worker. Belum ada panel parameter/grafik khusus
atau verifikasi otomatis data OPD dan asumsi kausal.

## Mencoba manual

Jalankan dari folder `backend` dengan Python yang memiliki Pydantic v2:

```bash
python -m app.simulation examples/simulation/fleet.json
```

Jika memakai virtualenv backend, aktifkan lingkungan tersebut terlebih dahulu.
Jika virtualenv belum diaktifkan, gunakan `.venv/bin/python` sebagai pengganti
`python` pada perintah tersebut.

Runner membaca JSON dan mencetak hasil ke terminal; tidak menulis database atau
memanggil model AI. Hasil memuat definisi input, provenance, parameter efektif,
output baseline, dan perbandingan setiap skenario. Field JSON memakai camelCase.

Hasil acuan `fleet * capacity`:

| Kondisi | Armada | Kapasitas per armada | Total | Selisih | Persentase |
|---|---:|---:|---:|---:|---:|
| Baseline | 10 | 5 | 50 | — | — |
| add_two | 12 | 5 | 60 | 10 | 20% |
| add_five | 15 | 5 | 75 | 25 | 50% |

Edit `scenarios[0].overrides.fleet.value` menjadi `14` dan jalankan ulang: hasil
skenario pertama menjadi `70`, selisih `20`, persentase `40`. Skenario kedua
tetap `75` karena setiap skenario dimulai dari baseline, bukan skenario sebelumnya.
Kembalikan nilai ke `12` setelah mencoba agar contoh tetap sesuai hasil acuan.

Kasus lain yang dapat dicoba, satu per satu lalu dikembalikan:

- Baseline armada `0`: output baseline `0`, delta tetap dihitung, persentase `null`.
- Rumus `fleet / 0`: error `division_by_zero`, output `total`, scope baseline.
- Nilai override armada `101`: error `invalid_input` karena melewati maksimum `100`.
- Rumus `unknown * capacity`: error `unknown_parameter`.
- Rumus `fleet ** 2` atau `open('file')`: error `unsupported_expression`.

Jika input atau perhitungan gagal, runner mengeluarkan JSON error ke stderr dan
exit code `2`; tidak ada JSON sukses parsial pada stdout.

## Alur dan batas engine

`SimulationDefinition` → snapshot dan validasi ulang → parse semua rumus → hitung
baseline → terapkan override setiap skenario → hitung output dan perbandingan →
`SimulationResult`.

`ValueOrigin` disimpan untuk baseline dan override, termasuk parameter efektif
yang diteruskan tanpa perubahan. Referensi sumber adalah deklarasi, bukan bukti
bahwa sumber eksternal telah diverifikasi.

Rumus mendukung konstanta numerik, nama parameter, `+`, `-`, `*`, `/`, unary
`+`/`-`, dan tanda kurung. Parser AST hanya menginterpretasi node yang diizinkan;
tidak memakai `eval`, `exec`, atau menjalankan kode Python dari input. Referensi
antaroutput dan fungsi belum didukung. Batas ekspresi: 1024 karakter, 128 node AST,
dan kedalaman 24 edge dari root.

Persentase = `(scenario - baseline) / baseline * 100`, dengan denominator bertanda.
Pada baseline nol, persentase `null`. Pada baseline negatif, tanda persentase perlu
ditafsirkan bersama delta. Semua hasil antara dan perbandingan harus finite.
Satu kegagalan membatalkan seluruh pemanggilan tanpa mengubah input.

Perhitungan memakai floating point tanpa pembulatan tampilan; ini belum merupakan
engine akuntansi presisi desimal. Satuan, rumus kausal, dan kelayakan asumsi belum
divalidasi otomatis. Ukuran total definisi belum dibatasi engine lokal; adapter
tool chat menerapkan batas payload dan jumlah skenario/output di atas.

## Tes terfokus

Dari `backend`, dengan lingkungan yang memiliki Pydantic v2 dan pytest:

```bash
python -m pytest --noconftest tests/test_simulation_schemas.py tests/test_simulation_engine.py -q
```

`--noconftest` memisahkan unit test ini dari fixture startup aplikasi, migrasi,
embedding, dan Qdrant. Tes ini tidak membuktikan integrasi chat atau layanan AI.

Tes adapter dan registry/loop chat memakai dependensi backend ringan yang
terkait (`pydantic-settings`, SQLAlchemy, OpenAI client, Qdrant client, MCP,
httpx dan jsonschema). Gunakan environment backend yang sudah dipasang sesuai
README. Tambahkan `jsonschema` ke environment tes bila belum tersedia (MCP
umumnya sudah membawanya sebagai dependensi transitif). Dari `backend`:

```bash
DATABASE_URL=sqlite:///:memory: MODEL_BASE_URL=http://127.0.0.1:9/v1 \
  python -m pytest --noconftest tests/test_simulation_schemas.py \
  tests/test_simulation_engine.py tests/test_simulation_tool.py \
  tests/test_chat_simulation.py tests/test_prompt.py -q
```

Tes chat memakai registry/loop aktual, SQLite in-memory, dan model berskrip;
tidak menyalakan lifespan aplikasi, menjalankan migrasi, atau menghubungi layanan
AI nyata. Ini membuktikan wiring, event, penanganan error dan penyimpanan pesan,
tetapi belum mengukur pemahaman bahasa natural atau mutu jawaban model produksi.

Tes klarifikasi termasuk fixture HTTP terisolasi tanpa lifespan produksi:

```bash
DATABASE_URL=sqlite:///:memory: MODEL_BASE_URL=http://127.0.0.1:9/v1 \
  python -m pytest tests/test_simulation_schemas.py tests/test_simulation_engine.py \
  tests/test_simulation_tool.py tests/test_simulation_clarification.py \
  tests/test_chat_simulation.py tests/test_chat_loop.py tests/test_chat_turns.py \
  tests/test_prompt.py -q
```

Fixture HTTP menguji auth, ownership, pilihan kedaluwarsa, SSE, dan penyimpanan
pesan dengan DB in-memory. Migrasi tetap perlu diverifikasi terpisah pada DB uji.
