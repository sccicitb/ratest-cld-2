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
menghitung → hasil JSON kembali ke loop/model → jawaban perhitungan disusun backend
dan dikirim melalui SSE. Tool
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
snapshot, acuan lanjutan memakai `activeScenarioId`, bukan posisi daftar.
Hasil tunggal otomatis aktif; hasil dengan beberapa alternatif selesai tanpa
pending atau permintaan memilih acuan. Saat user meminta kelanjutan yang ambigu,
`clarify_simulation(parameterId, amount)` membuat pilihan baseline dan skenario.
Jika user secara khusus meminta memilih skenario untuk dilanjutkan, pemanggilan
tanpa argumen membuat `select_scenario`. Memilihnya hanya menyimpan ID; baseline,
rumus dan hasil tidak dihitung ulang. Jawaban natural memakai
`resolve_simulation_clarification` dengan ID opsi yang sedang ditampilkan.
Ordinal seperti “yang kedua” harus mengikuti urutan opsi pending, bukan alias tetap.
Sebelum user menjawab pada turn berikutnya, model tidak boleh menyelesaikan
klarifikasi yang baru dibuat sendiri.

Untuk state lama tanpa ID aktif: satu skenario tetap dapat dipakai karena tidak
ambigu; beberapa skenario meminta pilihan. Pending penambahan lama yang tidak
menyimpan ID skenario dikonfirmasi ulang bila snapshot berisi beberapa alternatif.
Klik lama tersebut membuat pertanyaan baru tanpa menghitung; ID pertanyaan lama
kemudian kedaluwarsa. Opsi baru menyimpan `scenarioId`, sehingga mengubah urutan
hasil tidak mengubah acuan. Alias wire `last_scenario` dipertahankan untuk opsi
acuan aktif, tetapi tidak lagi berarti elemen terakhir.

`clarify_simulation` dengan `parameterId`, `amount` dan `reference` memproses
penambahan dari acuan eksplisit tanpa konfirmasi ulang. Acuan memakai `baseline`,
`last_scenario` (hanya jika identitas aktif jelas), atau `scenario:<ID tersimpan>`.
Jalur langsung dan penyelesaian pilihan berbagi `_calculate_change`; tidak ada
engine kedua. Batas nilai dan error perhitungan tetap diperiksa sebelum menyimpan.
Tanpa reference, pilihan penambahan menyertakan baseline dan skenario aktif atau
semua alternatif jika belum ada ID aktif. Pending dari sesi lama tetap bisa dijawab
atau dibatalkan; tidak dihapus diam-diam ketika reload.

Uji manual regresi identitas:
1. Minta dua alternatif sintetis: target 4 armada berkapasitas 6 ton/hari/armada,
   dan pembanding 17 armada berkapasitas 5. Baseline tetap 10 × 5 = 50.
2. Setelah hasil muncul, pastikan tidak ada pertanyaan atau label acuan aktif.
3. Reload, lalu kirim “sekarang coba tambah lima”. Pilihan baseline, target, dan
   pembanding muncul; target menampilkan 4 + 5 → 9. Pembanding 17 bukan default.
4. Pilih acuan target tersebut: hasil 9 × 6 = **54 ton/hari**, baseline **50**,
   selisih **4**, perubahan **8%**. Parameter kapasitas tetap mengikuti target.
5. Klik ulang pilihan yang sudah dipakai ditolak. Memilih opsi pada chat berbeda
   juga harus ditolak oleh pemeriksaan sesi/kepemilikan.

`GET /api/sessions/{id}/simulation` menyediakan pending untuk UI/reload. Klik
mengirim `simulationSelection: {questionId, optionId}` pada endpoint chat yang
sama. Pilihan yang sudah dijawab/kedaluwarsa ditolak. `run_simulation` tidak boleh
menimpa snapshot saat pending; tool code execution juga diblokir dalam turn selama
pending. Pertanyaan klarifikasi serta tabel hasil simulasi awal dan
penyelesaian dibuat server agar model tidak mengubah angka atau menambahkan
perbandingan lain.

Batas tahap ini: penambahan satu parameter dengan acuan baseline/alternatif
aktif. Penyelesaian klarifikasi penambahan ini belum mendukung operasi set-total, persen, atau beberapa parameter dalam
satu penyelesaian. Untuk permintaan di luar scope, model diarahkan mengklarifikasi
atau menawarkan pembatalan. Pengenalan ambiguitas dan interpretasi jawaban natural
masih bergantung pada LLM; validasi bukan jaminan semua kalimat dipahami. State
mengikuti batas konkurensi registry turn aplikasi saat ini (satu proses); belum
menggunakan penguncian lintas worker. Belum ada panel parameter/grafik khusus
atau verifikasi otomatis data OPD dan asumsi kausal.

## Kontrak input tool dan pembungkus kompatibilitas

Schema yang ditawarkan ke model tetap satu `SimulationDefinition` pada tingkat
teratas: `title`, `objective`, `parameters`, `outputs`, dan `scenarios`. Daftar
parameter/output/skenario harus berupa array objek; angka harus finite dan bertipe
angka sesuai validator yang sudah ada. Tidak ada perubahan kontrak engine/CLI.

Adapter `RunSimulation.execute` juga menerima dua bentuk kompatibilitas runtime:
`{"definition": <objek definisi lengkap>}` atau
`{"arguments": <objek definisi lengkap>}`. Pembungkus hanya boleh satu tingkat dan
menjadi satu-satunya field teratas. Setelah dibuka, isinya masuk ke validator,
pengecekan jumlah koleksi, engine, penyimpanan dan penyaji hasil yang sama.
Input asli tidak dimutasi. Schema untuk model tetap mengarahkan penggunaan bentuk
langsung agar tidak menambah pilihan kontrak yang harus dipelajari model.

Contoh yang ditolak: pembungkus ditambah `scope`/field lain, pembungkus bertingkat,
kedua pembungkus sekaligus, isi pembungkus berupa string JSON atau array, serta
pembungkus lain seperti `payload`. Field `userId`, `sessionId` atau field tak
terdaftar di dalam definisi juga tetap ditolak. Tidak ada penggabungan field,
parsing string JSON di dalam field, konversi string menjadi angka, pengisian nilai
kosong, atau perbaikan rumus otomatis.

Batas 32 KiB dihitung pada JSON UTF-8 asli, termasuk pembungkus, sebelum isinya
dibuka. Batas parameter/output/skenario dan hasil tetap berlaku. Validasi sesi
serta penolakan perubahan saat klarifikasi pending tetap dijalankan terlebih dahulu.
Error `invalid_input` menyertakan `hint` bentuk field yang diharapkan, serta detail
`issues` dari validator bila tersedia, tanpa menambahkan echo input mentah.

Uji melalui chat biasa tidak dapat memaksa bentuk JSON tertentu dari LLM.
Regresi otomatis `test_simulation_tool.py` dan `test_chat_simulation.py` mengirim
kedua bentuk secara langsung dan menguji retry dari field array yang salah ke
pembungkus berisi definisi valid. Hasil, baseline, asal nilai, acuan, serta tampilan
harus sama dengan bentuk langsung. Ini membuktikan kompatibilitas adapter, bukan
jaminan semua variasi keluaran model atau pertanyaan natural akan diterima.

## Status perhitungan dan kegagalan tool

`chat/simulation_status.py` mencatat hasil tool simulasi selama satu turn, tanpa
mengubah snapshot atau menghitung ulang angka. `loop.py` memakai status ini untuk
menahan teks dan reasoning model pada putaran setelah tool simulasi gagal.
Model tetap menerima error untuk memperbaiki input dan mencoba ulang.

- Jika model berhenti atau mencapai batas iterasi sementara kegagalan belum
  teratasi, jawaban akhir dibuat server: alasan umum yang aman, status hasil baru,
  dan permintaan memperbaiki input atau menjawab klarifikasi. Teks/angka manual
  dari model pada putaran yang diblokir tidak dikirim sebagai jawaban atau disimpan.
- Hasil tool berformat rusak, status tidak dikenal, exception tool, atau klaim
  sukses tanpa `SimulationResult` yang valid diperlakukan sebagai kegagalan.
  Validator kontrak yang sudah ada digunakan kembali; tidak ada engine kedua.
- Putaran berikutnya yang seluruh pemanggilan simulasinya berhasil menghitung
  dapat memulihkan status. Hasil campuran sukses/gagal dalam satu putaran tetap
  diblokir; keberhasilan tool lain tidak menghapus kegagalan simulasi.
- Jika ada perhitungan yang sudah berhasil pada turn tersebut, hasilnya tetap
  tersimpan dan jawaban menjelaskan bahwa seluruh permintaan belum selesai.
  Jika belum ada perhitungan baru yang berhasil, jawaban membedakan snapshot lama
  dari hasil permintaan sekarang. Tidak ada rollback atas perhitungan yang sah.
- Klarifikasi, pemilihan acuan, dan pembatalan tetap memakai balasan server yang
  sudah ada. Menyelesaikan klarifikasi tidak selalu berarti menghitung hasil baru.

Batasnya: ini memverifikasi status tool dan kontrak hasil, bukan pemahaman maksud
pengguna atau validitas data/rumus yang diberikan. Chat yang tidak
memanggil tool simulasi belum tercakup pengaman ini. Streaming awal sebelum error
diketahui mengikuti perilaku chat sebelumnya; teks interim dibersihkan UI saat
pemanggilan tool dimulai. Tidak ada pemeriksaan semantik terhadap artifact HTML.

Uji manual melalui Citya setelah restart backend:

1. Jalankan simulasi sintetis armada dengan rumus valid untuk memastikan jalur
   sukses dan pilihan acuan tetap bekerja.
2. Pada chat baru, minta simulasi sintetis dengan rumus `fleet / 0` dan parameter
   `fleet = 10`; minta sistem memakai engine tanpa mengganti rumus. Jika model
   memanggil tool dan perhitungan ditolak, jawaban akhir harus menjelaskan masalah
   pembagian nol, bukan mengklaim hasil numerik berhasil. Model boleh meminta
   klarifikasi lebih dahulu; itu bukan reproduksi error tool.
3. Kirim rumus valid `fleet * capacity`, `capacity = 5`, dan skenario `fleet = 12`.
   Jika input lengkap dan berhasil, hasil baseline 50 dan skenario 60 boleh tampil.

Pengujian otomatis di `test_chat_simulation.py` sengaja memakai model berskrip
untuk memaksa klaim sukses setelah kegagalan, retry, hasil campuran, respons rusak,
exception dan batas iterasi; tidak bergantung pada kemauan LLM memanggil rumus
yang tidak valid. Suite juga memeriksa token/reasoning SSE dan pesan tersimpan.

## Penyajian hasil perhitungan

`simulation/presentation.py::render_simulation` adalah satu penyaji Markdown untuk
simulasi awal dan hasil setelah klarifikasi. Ia menerima `SimulationResult` yang
sudah dihitung; tidak menghitung ulang delta, persentase, atau perbandingan lain.
Jawaban diawali judul/tujuan, kategori asal input, ringkasan baseline dan satu tabel
per output yang memuat semua alternatif. Nilai output, selisih dan persentase
langsung memakai field engine; tanda + hanya format tampilan. Asumsi dan batasan
mendahului detail rumus, parameter baseline dan asal nilai. Detail alternatif
hanya memuat input dengan nilai atau asal yang berbeda dari baseline. Input
warisan yang identik tidak ditampilkan ulang; perubahan asal dengan angka sama
tetap ditampilkan agar jejak sumber tidak hilang.
Persentase `null` ditulis tidak terdefinisi karena baseline nol. Angka float
mengikuti representasi Python tanpa pembulatan tersembunyi ke enam digit; nol
bertanda ditampilkan sebagai 0. Ini bukan perubahan presisi engine.

Status per-turn menyimpan hasil tervalidasi dari putaran perhitungan sukses terbaru,
bukan mengambil snapshot lama sebagai hasil baru. Beberapa panggilan sukses dalam
putaran yang sama ditampilkan sebagai laporan terpisah; jika putaran berikutnya
menghitung revisi, laporan memakai hasil revisi terbaru. Ketika status masih gagal,
pesan kegagalan tetap diutamakan. Balasan server untuk pemilihan/pembatalan dan
pertanyaan klarifikasi tetap digunakan seperti sebelumnya.

Setelah ada perhitungan berhasil, teks dan reasoning model ditahan. Model masih
bisa memanggil tool untuk koreksi atau melanjutkan pekerjaan, tetapi jawaban
perhitungan akhirnya berasal dari penyaji server. Narasi tambahan dan hitungan
manual dari model tidak disertakan. Metadata dari definisi tetap merupakan
input/deklarasi model atau pengguna, bukan fakta domain yang otomatis tervalidasi;
teksnya di-escape agar tidak membentuk HTML atau baris tabel tambahan.

Uji manual:
1. Jalankan contoh armada 10 × 5 dengan alternatif 12 dan 15 armada. Pastikan kedua
   baris perbandingan menampilkan 50 → 60 (+10; +20%) dan 50 → 75 (+25; +50%). Rumus, satuan,
   parameter serta asumsi harus muncul tanpa perbandingan tambahan buatan model.
2. Kirim “tambah lima”; pilih acuan lewat tombol atau chat, lalu reload. Tabel hasil
   lanjutan memakai format yang sama dan tetap tersedia pada riwayat pesan.
   Pada sesi baru dengan perbandingan awal yang sama, kirim “lanjutkan skenario
   tambah dua, tambah lima armada dari skenario itu”: langsung 17 × 5 = 85 tanpa
   pertanyaan acuan ulang.
3. Uji baseline 0 dengan rumus valid dan skenario nonnol. Persentase ditampilkan
   sebagai tidak terdefinisi, bukan 0%.

Chat konseptual yang tidak memanggil tool perhitungan tetap memakai jawaban model.
Jawaban historis tidak ditulis ulang; tidak ada perubahan schema DB. Frontend
memakai `ChatMarkdown` bersama untuk streaming/riwayat, tabel bergulir horizontal
pada ruang sempit, dan tidak menampilkan label acuan aktif permanen.

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

## Pilot penerimaan sintetis

[Panduan pilot persampahan](../../../docs/pilots/persampahan.md) menetapkan tujuan,
satuan, batas model dan prosedur UAT. Kumpulan percakapan memakai fixture armada
yang sudah ada; runner `scripts/evaluate_policy_pilot.py` memanggil loop/tool asli
pada DB in-memory. Tanpa `--live`, runner tidak menghubungi model.

[Hasil evaluasi pertama](../../../docs/pilots/persampahan-results.md) memisahkan
pemeriksaan angka/state dari review transcript. Semua kondisi mekanis lolos pada
satu eksekusi 10 kasus, tetapi pilot belum lulus: nilai absolut -2 ditafsirkan
sebagai pengurangan 2, dan asumsi eksplisit tidak selalu tersimpan lengkap. Review
OPD serta UAT browser tetap diperlukan; hasil sintetis bukan validasi model kota.


## Kartu visual dan grafik dalam chat

Hasil baru menyertakan `simulationResults` pada pesan assistant, berupa daftar
`SimulationReport`: satu `SimulationResult` tervalidasi serta `referenceChange`
opsional untuk perhitungan lanjutan. Kolom JSON nullable `messages.simulation_results`
ditambahkan oleh migration `e91a4f7c62b0`. Startup backend menjalankan migration
tertunda. Tidak ada backfill: pesan lama tetap Markdown; hasilnya tidak direka ulang
dari snapshot sesi yang sudah berubah.

Alur data: engine → status per-turn / hasil resolusi klik → payload pesan → commit
DB → event SSE `simulation_result` → `AssistantContent` → `SimulationResultCard`.
Endpoint messages mengembalikan payload yang sama saat reload. `SimulationStatus`
memilih hasil sukses pada round terakhir; kegagalan tidak mendapat grafik. Pertanyaan,
pemilihan acuan dan pembatalan tanpa perhitungan juga tidak mendapat payload visual.
Beberapa perhitungan sukses dalam round yang sama menghasilkan beberapa kartu.

`ToolContext.simulation_report` membawa hasil engine untuk jalur kelanjutan eksplisit
atau klik tanpa membaca ulang snapshot. Nilainya direset pada awal turn.
`schema_base.py` memuat `CamelModel` yang sama dengan sebelumnya; ia tetap diekspor
melalui `app.schemas`, dan digunakan kontrak simulasi tanpa circular import DTO pesan.

Frontend memakai satu `AssistantContent` untuk stream dan riwayat. Zod memeriksa
bentuk payload, finite numbers, ketersediaan input/output dan konsistensi baseline.
Jika payload kosong atau tidak didukung, teks Markdown tetap tersedia. Tidak ada
pembacaan angka dari Markdown, penghitungan delta/persentase baru, HTML buatan
model, atau dependency grafik tambahan.

Kartu menampilkan label asal input, pilihan indikator, angka per skenario, grafik
batang SVG / tabel, asumsi, keterbatasan, dan detail perhitungan yang dapat dibuka.
Perbandingan memakai baseline; panjang batang memakai skala bersama yang mencakup
nol dan nilai negatif. Normalisasi skala mencegah overflow geometri untuk float
finite yang sangat besar. Angka tetap mengikuti representasi JavaScript tanpa
pembulatan tambahan; pemisah ribuan/desimal biasa ditampilkan dalam format Indonesia.
Persentase null tidak diganti 0%. Warna bukan penilaian baik/buruk suatu kebijakan.

Uji manual setelah restart backend dan refresh frontend:

1. Buat simulasi baru 10 × 5 dengan alternatif 12 dan 15 armada. Kartu harus
   menampilkan 50/60/75 ton/hari, selisih +10/+25, perubahan +20%/+50%.
2. Ganti Grafik ke Tabel; angka harus sama. Buka detail untuk rumus dan asal nilai.
3. Minta dua output dengan satuan berbeda; pilih indikator. Grafik, kartu angka
   dan tabel harus serentak mengikuti output yang dipilih, tanpa mencampur satuan.
4. Kirim kelanjutan ambigu, pilih skenario 12 armada lalu tambah 5: hasil baru 85.
   Reload dan buka pesan awal: grafik awal harus tetap 50/60/75.
5. Uji kondisi awal nol dengan rumus valid; persentase tetap tidak terdefinisi.
   Uji output negatif dengan rumus yang sesuai; batang harus mengarah dari nol
   ke sisi negatif. Coba juga seluruh nilai nol.
6. Pada layar sempit, kartu menumpuk dan tabel dapat digulir. Gunakan Tab/Enter
   untuk tombol dan detail. Pertanyaan klarifikasi, pembatalan serta perhitungan
   gagal harus tetap terbaca dan tidak mengklaim hasil baru dengan grafik.

Verifikasi otomatis frontend tanpa dependency tes baru (Node >=22.22):

```bash
cd frontend
node --test tests/simulation.test.mjs
npm run typecheck
npm run build
```

Tambahkan `tests/test_simulation_message_migration.py` pada suite backend.
Tes migration membuat database sementara dari revisi sebelum kartu visual,
memastikan pesan lama tetap utuh dan payload nullable, kemudian menguji downgrade
hanya pada database sementara itu. Tes chat/HTTP mencocokkan SSE, pesan tersimpan,
hasil klik, kegagalan dan histori setelah simulasi lanjutan.

Preview komponen di Chrome telah memeriksa Grafik/Tabel, pilihan indikator, detail,
lebar kartu 320 piksel, kondisi awal nol dan batang negatif/positif. Preview memakai
fixture sintetis terisolasi, bukan login atau UAT end-to-end pada database Citya.
