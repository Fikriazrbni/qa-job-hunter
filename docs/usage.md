# QA Job Hunter — program tanpa AI

Program Python untuk memantau job board publik, menyaring QA Automation/SDET, menyimpan riwayat dan menghasilkan laporan lokal. Tidak memakai model AI, OpenAI API, API key, subscription AI, database server, atau package pihak ketiga saat berjalan. Python 3.11+ dan koneksi internet diperlukan.

## Jalankan

Di Terminal:

```sh
cd '/path/to/qa-job-hunter'
python3 -m jobhunter run
python3 -m jobhunter status
```

Laporan utama: `output/automatic/latest.html` (buka sebagai file di browser). Data lengkap: `latest.json`; versi teks: `latest.md`. Setiap run juga memiliki file arsip `run-00001.*`. Program tidak mengirim email atau CV, tidak auto-apply dan tidak berinteraksi dengan akun LinkedIn.

Kode exit: **0** semua sumber berhasil; **2** sebagian berhasil (laporan menyebut sumber gagal); **1** semua gagal/konfigurasi atau eksekusi bermasalah. API error tidak disamarkan sebagai “tidak ada lowongan”.

## Jadwal yang dipilih

Konfigurasi awal Anda: **setiap 3 hari kalender, pukul 09.00 Asia/Jakarta**. Run live manual pertama juga menjadi anchor jadwal. Contoh: setelah run selesai pada 29 Oktober, target berikut adalah **1 November pukul 09.00 WIB**. Run manual berikutnya menggeser anchor; ubah hanya bila memang ingin mengatur ulang jadwal.

```sh
python3 -m jobhunter schedule prepare
python3 -m jobhunter schedule install
python3 -m jobhunter status
```

`prepare` membuat plist yang bisa diperiksa di `output/scheduler/`; `install` memasangnya ke `~/Library/LaunchAgents/com.fikri.qa-job-hunter.plist` dan memverifikasi `launchctl`. Scheduler memakai Homebrew Python lokal yang terdeteksi, bukan Codex/AI. Ada pemicu kalender pukul09.00 dan pemeriksaan cadangan setiap15menit; keduanya hanya mencari bila interval3hari sudah jatuh tempo. Eksekusi tetap dapat terlambat karena kondisi sistem. Jam09.00 adalah waktu target, bukan jaminan real-time.

Mac harus menyala dan user login. Saat Mac tidur, pemeriksaan berikutnya setelah bangun akan melihat apakah sudah jatuh tempo dan menjalankan satu pencarian; tidak memutar ulang semua jadwal yang terlewat. Saat Mac mati atau logout program tidak berjalan. Codex tidak perlu tetap terbuka. macOS dapat meminta izin akses folder Documents untuk Python; periksa log bila scheduler gagal membaca proyek. Scheduler lokal tidak membuat Mac bangun atau menyala sendiri.

```sh
python3 -m jobhunter tick
python3 -m jobhunter schedule uninstall
```

`tick` menjalankan hanya bila due. `uninstall` menonaktifkan scheduler khusus proyek ini, tanpa menghapus data/laporan. Log scheduler: `logs/scheduler.log` dan `logs/scheduler-error.log`.

Run **success atau partial** menjadi anchor berikutnya; laporan partial menyebut keterbatasan cakupannya. Jika semua sumber gagal, jadwal tidak dimajukan dan scheduler mencoba lagi setelah cooldown satu jam. File lock mencegah dua run bersamaan. Event temuan disimpan sebelum laporan dan baru ditandai delivered setelah laporan berhasil ditulis, agar kegagalan disk tidak menghilangkan temuan.

## Ganti konfigurasi

Edit `config.json`:

```json
{
  "timezone": "Asia/Jakarta",
  "interval_days": 3,
  "hour": 9,
  "minute": 0,
  "freshness_days": 7,
  "limit": 10
}
```

Potongan di atas menunjukkan field yang bisa diedit, bukan pengganti seluruh file. Untuk harian, ubah `interval_days` menjadi `1`. Konfigurasi dibaca ulang pada setiap tick; tidak perlu install ulang untuk interval. Jika mengubah jam, install ulang untuk memperbarui pemicu kalender (pemeriksaan15menit tetap membaca jam baru). Jika folder proyek atau interpreter Python dipindah/dihapus, install ulang scheduler dari lokasi/interpreter baru.

`profile.json` menyimpan skill yang dibuktikan CV dan tanggal mulai pengalaman, tanpa kontak pribadi atau PDF. Skill GitHub Actions/GitLab yang belum dibuktikan CV dipisahkan. Ini profil untuk program; perbarui JSON secara eksplisit. Selalu perbarui profil saat CV berubah.

## Sumber dan cakupan

Versi ini memantau **watchlist perusahaan pada `config.json`**, melalui tiga API publik yang didokumentasikan:

- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html): list published jobs; detail role QA untuk tanggal, deadline, salary dan pertanyaan eligibility.
- [Lever Postings API](https://github.com/lever/postings-api): list published postings dengan pagination.
- [Ashby Public Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api): published/listed jobs, lokasi, workplace dan compensation.

Watchlist awal: Alpaca, Sora Union, Canonical, Ping Identity, Flex, Binance, Circle, Bjak, Trust Wallet, Windranger, Salmon, DualEntry. Identifier diambil dari board yang sudah ditemukan pada pencarian terdahulu. Employer/tenant ownership tidak diverifikasi ulang secara independen setiap run. Tidak ada klaim pencarian seluruh internet atau seluruh lowongan Indonesia.

Untuk menambah perusahaan, ambil board identifier dari URL karier publiknya, lalu tambahkan entri:

```json
{"provider": "ashby", "board": "nama-board-dari-url", "company": "Nama Perusahaan"}
```

Provider yang didukung: `greenhouse`, `lever`, `ashby`. `enabled: false` menonaktifkan source tanpa menghapusnya. Program tidak menebak board baru, melewati CAPTCHA/login, atau scraping feed sosial. **Posting recruiter LinkedIn belum diambil otomatis pada versi ini.** CVAT memakai jalur email dan halaman sendiri, sehingga belum termasuk adapter API versi ini. Perlu adapter/sumber tambahan untuk memperluas cakupan tersebut; scheduler tidak menambah akses sumber.

Satu sumber gagal tidak membatalkan sumber lain. Respons malformed/terpotong/lebih64MB, error jaringan, dan pagination yang tidak selesai tidak direkonsiliasi sebagai hilangnya lowongan. Pengambilan dilakukan read-only GET dengan timeout, retry terbatas dan empat worker.

## Cara membaca hasil

Bagian **Baru / perubahan** berisi posting baru atau perubahan konten/eligibility/aplikasi, setelah deduplikasi. **Snapshot** dapat berisi hasil yang sudah pernah dilihat. Bagian **Tidak lagi tercantum** berarti tidak terlihat pada index publik yang berhasil diambil, bukan bukti pasti posisi Closed. Source gagal mempertahankan data lama dengan label belum dicek ulang.

Tanggal dibedakan secara eksplisit:

- Greenhouse `first_published`: tanggal publikasi jika tersedia; `updated_at` tidak dijadikan tanggal publikasi.
- Lever `createdAt`: tanggal pembuatan record, disimpan sebagai metadata; tidak diklaim sebagai tanggal publikasi.
- Ashby `publishedAt`: **last published**, bisa berarti republish, bukan lowongan pertama kali dibuat.
- First seen: pertama kali program melihatnya; tidak dijadikan bukti posting tujuh hari terakhir. Umur tidak tersedia tetap Not available. Kandidat lama yang masih listed dapat muncul di bawah hasil fresh.

Screening bersifat **rules-only**. Kata kunci bahasa/framework, scope engineering, negara, arrangement dan salary disusun sebagai bukti yang dapat ditinjau. Bagian requirements dibobotkan sebelum bonus jika header dikenali; parser tidak menjamin memahami semua required/preferred/alternatif. Nama skill yang belum terbukti selalu dilabeli sebagai *disebut JD*, bukan otomatis syarat wajib yang gagal.

Semua kandidat yang lolos filter awal tetap **Review**, tidak pernah auto-Apply. Label geografis Verified berarti aturan menemukan scope eksplisit di data role dan tidak menemukan pembatas yang dikenali; bukan jaminan entity, authorization, atau kecocokan seluruh persyaratan. Generic remote tidak dianggap worldwide. Ashby `isRemote=true` tidak mengalahkan `workplaceType=Hybrid`. Pola pembatas negara/office/CET diprioritaskan. Bahasa/lokasi yang tidak dikenali tetap bisa membutuhkan koreksi manusia.

Skor otomatis memakai bobot T35/R25/G15/L15/C10, tetapi bukan reproduksi skor analisis AI sebelumnya. Technical score dihitung dari kecocokan kata kunci dengan bobot core lebih besar; tidak menyimpulkan kemampuan dari istilah generik. Salary unknown/tidak comparable diberi C0,50; angka employer dipertahankan tanpa konversi, estimasi atau expected-salary buatan. Urutan: eligibility lebih pasti, lalu score, lalu freshness. Max10 membatasi tampilan, bukan jumlah posting yang diperiksa.

## Deduplikasi dan penyimpanan

`data/jobs.sqlite3` adalah state utama, ditambah lockfile. Provider + board + posting ID dipertahankan, sehingga country variant dengan ID berbeda tidak digabung. Tracking URL diabaikan, semantic parameter tetap dipertahankan. Pada run pertama, URL ATS yang dapat dikenali dari `job-hunter-history.md` diimpor agar tidak dianggap hasil baru. Import baseline hanya sekali; riwayat manual tidak pernah ditimpa. Role lama yang baru masuk snapshot mesin disupresi pada pemeriksaan pertama; ini tidak membuktikan status lama masih berlaku. Perubahan setelah snapshot mesin, termasuk reappearance, baru menghasilkan event. Riwayat yang hanya punya URL LinkedIn tidak dapat otomatis dipetakan ke req ATS lain.

Backup `data/` untuk mempertahankan riwayat. Menghapus database menghapus deduplikasi mesin dan anchor jadwal. Data/cache/laporan otomatis diabaikan Git. Proyek ini tidak mengunggah profil/CV ke penyedia API. Untuk GitHub Actions, ikuti [deployment cloud](cloud.md): database dan laporan disimpan di repo private, sedangkan kode boleh public.

## Pengujian

```sh
python3 -m unittest discover -s tests -v
```

Fixtures sintetis terpisah dari data live. Pengujian mencakup pagination/schema, tanggal, Indonesia/APAC/India/EMEA, hybrid, pertanyaan authorization, required vs bonus, kegagalan source, escaping HTML, deduplikasi, event yang bertahan saat report gagal, locking dan jadwal lintas bulan. Tidak diperlukan akses jaringan untuk unit tests.
