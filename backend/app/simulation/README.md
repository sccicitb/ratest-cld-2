# Structured simulation: stages 1–2

Engine lokal untuk membandingkan baseline dengan skenario alternatif. Belum
terhubung ke chat, API, database, atau UI. Semua angka pada contoh adalah sintetis.

## Mencoba manual

Jalankan dari folder `backend` dengan Python yang memiliki Pydantic v2:

```bash
python -m app.simulation examples/simulation/fleet.json
```

Jika memakai virtualenv backend, aktifkan lingkungan tersebut terlebih dahulu.
Lingkungan tes terisolasi sesi development ini juga dapat dipakai selama masih ada:

```bash
/tmp/citya-simulation-stage1-env/bin/python -m app.simulation examples/simulation/fleet.json
```

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
divalidasi otomatis. Ukuran total definisi belum dibatasi engine; adapter chat/API
nantinya perlu menerapkan batas payload dan jumlah skenario/output.

## Tes terfokus

Dari `backend`, dengan lingkungan yang memiliki Pydantic v2 dan pytest:

```bash
python -m pytest --noconftest tests/test_simulation_schemas.py tests/test_simulation_engine.py -q
```

`--noconftest` memisahkan unit test ini dari fixture startup aplikasi, migrasi,
embedding, dan Qdrant. Tes ini tidak membuktikan integrasi chat atau layanan AI.
