# AudiobookGdrive

Ứng dụng desktop **độc lập** (không phải plugin Calibre) để đồng bộ,
kiểm tra và quản lý thư viện **sách nói (audiobook)** trên Google Drive
của chính bạn, kèm ghép nối nhanh với app nghe sách nói **Voice** trên
Android qua link/mã QR.

Được tách riêng từ nhánh "Audiobook Sync" của plugin Calibre
**Calibre Gdrive Sync 2.7.7** bạn cung cấp — toàn bộ logic Google
Drive/OAuth/upload/kiểm tra/quản lý được giữ **nguyên vẹn**, chỉ thay
phần "vỏ" (cách lưu cấu hình, cách chạy nền, cửa sổ chính) từ plugin
nhúng trong Calibre sang 1 ứng dụng Windows chạy độc lập.

## Tính năng

- **Đăng nhập Google** ngay trong ứng dụng (OAuth2 + PKCE, mở trình
  duyệt để bạn xác nhận — không cần tự tạo Client ID/Secret, ứng dụng
  đã có sẵn 1 client dùng chung).
- **Đồng bộ Audiobooks lên Google Drive**: quét các thư mục gốc bạn
  cấu hình, mỗi thư mục con (chứa `metadata.opf`) được coi là 1 cuốn
  sách nói và được tải nguyên vẹn lên Drive (`Audiobooks/<Tên cuốn>/`),
  bỏ qua file không đổi (hash + size + mtime), tải song song nhiều file
  cùng lúc, tự retry khi mất mạng giữa chừng.
- **Kiểm tra Audiobooks trên Drive...**: đối chiếu `metadata_public.json`
  với thực tế trên Drive, tự sửa id mồ côi/thiếu, phát hiện cuốn không
  có audio, phát hiện cuốn bạn tự tải thủ công lên Drive.
- **Quản lý Audiobooks trên Drive...**: xem danh sách/lưới ảnh bìa, tìm
  kiếm, lọc, sắp xếp, sửa metadata, xoá (kèm tuỳ chọn xoá file thật trên
  Drive), xem chi tiết từng cuốn, đồng bộ lại metadata từ Drive.
- **Thêm thư viện sách nói vào Voice...**: tự động chia sẻ thư mục
  `Audiobooks/` ở chế độ "Anyone with the link", hiện link + mã QR để
  dán/quét trong app Voice trên điện thoại Android.
- Chạy dưới nền qua khay hệ thống (system tray) + thông báo khi hoàn
  tất, xem log chi tiết ngay trong ứng dụng.

## Cài đặt & chạy nhanh (Windows)

**Cách 1 — chạy trực tiếp bằng Python (khuyên dùng để phát triển/thử):**

1. Cài [Python 3.9+](https://www.python.org/downloads/) — nhớ tick
   **"Add python.exe to PATH"** lúc cài đặt.
2. Bấm đúp file **`run_AudiobookGdrive.bat`**.
   - Lần đầu chạy sẽ tự tạo môi trường ảo `.venv` và cài `PyQt5` (cần
     mạng, mất khoảng 1-2 phút).
   - Các lần sau mở lại rất nhanh.

**Cách 2 — đóng gói thành 1 file `.exe` độc lập (không cần cài Python
trên máy chạy cuối):**

1. Trên máy đang có Python (máy "build"), bấm đúp **`build_exe.bat`**.
2. Đợi build xong (vài phút), file kết quả nằm ở **`dist\AudiobookGdrive.exe`**.
3. Copy riêng file `.exe` này sang bất kỳ máy Windows 10/11 nào khác để
   chạy — không cần cài Python hay bất kỳ thư viện gì trên máy đó.

## Đăng nhập Google lần đầu

1. Mở ứng dụng, bấm **"Đăng nhập Google"**.
2. Trình duyệt mặc định sẽ tự mở trang đăng nhập Google — chọn tài
   khoản Google của bạn, bấm **Allow/Cho phép**.
   - Có thể thấy màn hình "Google hasn't verified this app" (do OAuth
     client chưa qua xét duyệt production của Google) — bấm
     **Advanced/Nâng cao** → **Go to AudiobookGdrive (unsafe)** để tiếp
     tục. Đây là hành vi bình thường với OAuth "Desktop app" tự dùng
     cho mục đích cá nhân, giống các công cụ mã nguồn mở khác
     (rclone, gdrive-cli...).
   - Ứng dụng chỉ xin quyền `drive.file` — **chỉ đọc/ghi những file/thư
     mục do chính nó tạo ra**, không đụng tới các file khác trong Drive
     của bạn.
3. Sau khi thấy "Google sign-in successful", quay lại ứng dụng — trạng
   thái tài khoản sẽ tự cập nhật.

Muốn dùng Google Cloud project OAuth **của riêng bạn** (ví dụ để có
quota API riêng) thay vì client mặc định đi kèm ứng dụng: mở **Cài
đặt → tab Nâng cao**, điền Client ID/Secret của bạn.

## Cấu hình thư mục Audiobook

1. Mở **Cài đặt → tab Audiobook Sync**.
2. Bấm **"Thêm..."**, chọn thư mục gốc trên máy tính chứa các cuốn
   sách nói (mỗi thư mục con trực tiếp bên trong là 1 cuốn, cần có file
   `metadata.opf`).
3. Có thể thêm nhiều thư mục gốc khác nhau.
4. Đóng Cài đặt, bấm **"Đồng bộ Audiobooks lên Google Drive"** ở màn
   hình chính.

## Ghép nối với app Voice trên Android

1. Đồng bộ ít nhất 1 lần (xem trên).
2. Bấm **"Thêm thư viện sách nói vào Voice..."** ở màn hình chính.
3. Trên điện thoại, mở app **Voice** → menu thư viện → chọn tính năng
   thêm thư viện qua QR/link → quét mã QR hiện trên màn hình máy tính
   (hoặc copy link/ID dán thủ công).
4. Mỗi lần đồng bộ sau này sẽ tự cập nhật `metadata_public.json`; làm
   mới/sync lại trong app Voice để lấy thay đổi mới nhất.

## Cấu trúc project

```
AudiobookGdrive/
├── main.py                      # entry point
├── requirements.txt
├── run_AudiobookGdrive.bat      # khởi động nhanh (Windows)
├── build_exe.bat                # đóng gói .exe (Windows, PyInstaller)
└── audiobookgdrive/
    ├── main_window.py           # cửa sổ chính
    ├── config.py                # prefs + cửa sổ Cài đặt
    ├── oauth.py                 # đăng nhập Google (OAuth2 + PKCE)
    ├── drive_api.py             # Google Drive API v3 (REST)
    ├── hash_utils.py            # so sánh file đã đổi hay chưa
    ├── jsonconfig.py            # lưu cấu hình/token dạng JSON (đĩa)
    ├── logger.py / log_dialog.py
    ├── tray.py / bg_jobs.py     # khay hệ thống + chạy nền
    ├── login_dialog.py
    ├── pairing_dialog.py / audiobook_pairing_dialog.py
    ├── qrcodegen.py             # vẽ mã QR thuần Python (vendor, MIT)
    ├── audiobook_dialog.py           # dialog "Đồng bộ..."
    ├── audiobook_check_dialog.py     # dialog "Kiểm tra..."
    ├── audiobook_manage_dialog.py    # dialog "Quản lý..."
    └── audiobook_sync/          # toàn bộ nghiệp vụ (không phụ thuộc Qt)
        ├── scanner.py, opf_parser.py, state_store.py, backup.py
        ├── metadata_io.py, sharing.py, checker.py
        ├── uploader.py, worker.py
        └── manage_ops.py, manage_worker.py
```

Cấu hình/token/log của ứng dụng được lưu ở:
`%APPDATA%\AudiobookGdrive\` (Windows).

## Ghi chú

- Đây là bản tách/refactor từ mã nguồn plugin Calibre bạn đã cung cấp
  (`CalibreGdriveSync-2_7_7-resync-from-drive.zip`) — toàn bộ nghiệp vụ
  Drive API/OAuth/upload/kiểm tra/quản lý đồng bộ **audiobook** được
  giữ nguyên vẹn 1:1, chỉ thay lớp lưu trữ cấu hình (`JSONConfig` của
  Calibre → JSON file riêng) và lớp giao diện chạy nền (Jobs list của
  Calibre → khay hệ thống).
- Chiều đồng bộ hiện tại chỉ hỗ trợ **Upload** (máy tính → Drive), như
  đúng plugin gốc — 2 chiều/download sẽ cần phát triển thêm ở bản sau.
