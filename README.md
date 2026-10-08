# Veyra Image Viewer

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Veyra adalah **desktop image viewer** untuk Windows yang meniru gaya aplikasi image viewer klasik (FastStone-like). Veyra menggabungkan backend Python berbasis web dengan shell desktop native, sehingga UI-nya ringan, responsif, dan terasa seperti aplikasi Windows asli.

## Tujuan & Fungsi Utama

Veyra dirancang untuk:

- Menjelajahi folder lokal dan drive Windows dalam tampilan tree.
- Melihat koleksi gambar dalam **Image Grid / Thumbnail View**.
- Membuka gambar dalam **Image View / Full Screen** dengan zoom, pan, dan navigasi.
- Mengelola file gambar: copy, cut, paste, rename, hapus, batch rename, dan buat folder baru.
- Mengelompokkan gambar ke dalam **Group** untuk filter dan organisasi.
- Melindungi file gambar sensitif melalui format enkripsi **.aimg**.

## Teknologi & Framework Utama

| Lapisan | Teknologi |
|--------|-----------|
| Backend | Python 3.11+, [Flask](https://flask.palletsprojects.com/) |
| Framework Aplikasi | [Betrayer](https://github.com/adigayung/betrayer) — lifecycle, DI, dan data layer |
| Desktop Shell | [pywebview](https://pywebview.flowrl.com/) dengan WebView2 (native Windows) |
| Frontend | Single-file vanilla HTML/CSS/JS (`veyra/index.html`) |
| Database | SQLite melalui Betrayer Data Layer (group & file identity) |
| Keamanan | `cryptography`, `argon2-cffi`, DPAPI Windows (native boundary) |
| Parsing Metadata | Native header parser untuk JPEG/PNG/GIF/BMP/WebP/SVG (tidak bergantung penuh pada Pillow) |

## Fitur yang Tersedia Saat Ini

### Image Grid / Thumbnail View

- Thumbnail grid yang menampilkan gambar dari folder aktif.
- Metadata per item: nama, dimensi (jika tersedia), tipe file, ukuran, dan badge **🔒** untuk file `.aimg`.
- Multi-seleksi dengan **Ctrl+Klik** dan **Shift+Klik**.
- Navigasi folder melalui sidebar File Explorer tree yang mendukung drive Windows dan nested folder.
- Path bar dengan tombol Back, Forward, Up, dan Refresh.

### Image View / Single Image View

- Buka gambar dengan **double-click** pada thumbnail atau **Open Full Screen** dari context menu.
- Dukungan format: `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.webp`, `.svg`, serta `.aimg` setelah session unlock.
- **Single click**: toggle antara *Actual Size* dan *Fit Width*.
- **Double click**: keluar dari Image View dan kembali ke grid.
- **Mouse wheel**: navigasi ke gambar berikutnya/sebelumnya sesuai urutan grid yang sedang aktif.
- **Drag**: pan/zoom image dengan clamping agar tidak keluar dari viewport.
- Tombol toolbar zoom in/out (🔍＋ / 🔍－) juga aktif.

### Grid Virtualization untuk Dataset Besar

Image Grid menggunakan virtualisasi berbasis window:

- Hanya ~61 item di sekitar viewport center yang di-mount ke DOM (`GRID_WINDOW_RADIUS = 30`).
- Scroll trigger di-debounce (`GRID_SCROLL_DEBOUNCE = 250 ms`) dan render window melompat langsung ke posisi baru, bukan di-scroll satu per satu.
- Ukuran scrollable total tetap dipertahankan melalui spacer pads, sehingga scrollbar tetap akurat meski dengan ribuan gambar.
- Performa sorting dan filtering tidak memengaruhi jumlah DOM yang di-mount.

### Sort By

Toolbar **Sort By** mengurutkan seluruh dataset aktif (folder atau group) berdasarkan metadata nyata:

- **Date** (`date_desc` / `date_asc`)
- **File Name** (`name_asc` / `name_desc`)
- **File Size** (`size_desc` / `size_asc`)

Urutan sort yang dipilih juga menjadi urutan navigasi mouse wheel di Image View.

### Group / Filter

Panel **Groups** di sidebar memungkinkan:

- Membuat, rename, dan menghapus group.
- Menambah/menghapus file terpilih ke/dari group.
- Melihat anggota group dalam panel detail.
- **Group filter** di toolbar: pilih `Normal` untuk melihat folder aktif, atau pilih group untuk memfilter grid hanya ke member group tersebut.
- Identitas file di database berbasis content-stable-id, sehingga rename/move tetap mempertahankan keanggotaan group.

### File Operations

Context menu klik-kanan dan backend API menyediakan:

- **Copy / Cut / Paste** clipboard antar-folder.
- **Rename** file tunggal.
- **Batch Rename** dengan prefix, suffix, numbering, dan padding.
- **Copy to Folder** / **Move to Folder**.
- **Remove** (delete) satu atau banyak file.
- **New Folder**.

Semua operasi file diurus oleh `FileOpsService` dan sinkron dengan keanggotaan group melalui `GroupService`.

### AIMG Encryption / Security

Veyra menyediakan format enkripsi khusus `.aimg`:

- **Format**: header autentikasi + thumbnail terenkripsi + payload ciphertext.
- **Cipher**: AES-256-GCM dengan nonce unik per file.
- **Key derivation**: password → Argon2id → KEK → unwrap master key. Sub-key per file dihasilkan dengan HKDF-SHA256.
- **Keystore**: disimpan di `.veyra/keystore.bin`, di-lindungi DPAPI di Windows sehingga file keystore tidak dapat dibuka di user/machine lain.
- **Session-based**: user unlock session dengan password; selama terkunci, thumbnail maupun gambar `.aimg` tidak dapat dilihat.
- **No temp file**: plaintext `.aimg` hanya ada di memory saat ditampilkan (`/api/image`, `/api/thumb`).
- Enkripsi/dekripsi dapat dilakukan per folder secara rekursif melalui dialog 🔐 di toolbar.

### Native Desktop Shell

- Veyra berjalan sebagai jendela desktop native via **pywebview / WebView2**, bukan browser eksternal.
- Window dibuka dalam mode **maximized** tanpa address bar, tab, atau browser chrome.
- Native fullscreen diatur melalui bridge `window.__aether_fs` yang memanggil `window.toggle_fullscreen()` dari Python.
- Backend Flask dijalankan di daemon thread (`127.0.0.1:8349`) dan dimatikan bersih saat window ditutup.

## Requirements / Dependencies

- Python 3.11 atau lebih baru
- Windows (dengan WebView2 Runtime)
- Virtual environment direkomendasikan

Lihat `requirements.txt`:

```text
betrayer
Flask>=3.0
pywebview
cryptography>=42
argon2-cffi>=23
pillow>=10
```

## Cara Instalasi

1. Clone repository:

```bash
git clone <repo-url>
cd Veyra
```

2. Buat virtual environment:

```bash
python -m venv venv
```

3. Install dependencies:

```bash
venv\Scripts\python.exe -m pip install -r requirements.txt
```

> Pastikan WebView2 Runtime sudah terinstal di Windows. Runtime ini biasanya sudah ada di Windows 10/11.

## Cara Menjalankan Veyra

### Opsi 1: Melalui `run.bat` (direkomendasikan)

Double-click atau jalankan dari Command Prompt:

```bash
run.bat
```

Batch file ini akan:
- Menggunakan interpreter dari `venv\Scripts\python.exe`.
- Memeriksa dependency utama (`flask`, `betrayer`, `webview`).
- Menjalankan `desktop.py`.

### Opsi 2: Melalui Python langsung

```bash
venv\Scripts\python.exe desktop.py
```

### Opsi 3: Development server (tanpa shell desktop)

```bash
venv\Scripts\python.exe run.py
```

Kemudian buka browser di `http://127.0.0.1:8349/`. Catatan: beberapa fitur native fullscreen hanya tersedia saat dijalankan melalui `desktop.py`.

### Opsi 4: WSGI entry point

```bash
venv\Scripts\python.exe wsgi.py
```

## Testing / Verification

Veyra memiliki serangkaian skrip verifikasi di root project. Skrip ini dapat dijalankan untuk memastikan fitur-fitur utama tetap stabil:

```bash
venv\Scripts\python.exe verify_desktop_startup.py
venv\Scripts\python.exe verify_grid_virtualization.py
venv\Scripts\python.exe verify_viewimage_interaction.py
venv\Scripts\python.exe verify_fullscreen.py
venv\Scripts\python.exe verify_viewer_ops.py
venv\Scripts\python.exe verify_contextmenu.py
venv\Scripts\python.exe verify_files_actions.py
venv\Scripts\python.exe verify_groups.py
venv\Scripts\python.exe verify_groupsort.py
venv\Scripts\python.exe verify_sortby.py
venv\Scripts\python.exe verify_aimg.py
```

> Beberapa skrip verifikasi menggunakan browser headless dan/atau filesystem sementara. Jalankan dari root project dan pastikan virtual environment aktif.

## Status Project

Veyra saat ini berada dalam **tahap aktif pengembangan** (versi `0.1.0`). Fungsi inti image viewer, file operations, group management, AIMG encryption, dan native desktop shell sudah tersedia dan telah melewati regression suites.

Project ini open source di bawah lisensi [MIT](LICENSE).
