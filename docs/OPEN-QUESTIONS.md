# Câu hỏi mở

Các điểm cần trao đổi và chốt **trước khi viết code**. Khi chốt, ghi quyết định ở đây và (nếu là quyết định kiến trúc) tạo ADR trong `adr/`.

| # | Câu hỏi | Lựa chọn | Đề xuất | Trạng thái |
|---|---|---|---|---|
| Q1 | Định hướng giao diện "macOS native" | A. Phong cách macOS trên Windows · B. App macOS thật | A | ✅ **A** (2026-10-03) — [ADR-0002](adr/0002-macos-style-ui-pywebview.md) |
| Q2 | Vỏ ứng dụng | Tab trình duyệt · pywebview · Tauri · Electron | pywebview | ✅ **pywebview** (2026-10-03) |
| Q3 | Chính sách dependency | Chỉ thư viện chuẩn · cho phép vài thư viện nhỏ (pywebview, pystray) | Cho phép, nếu chọn Q2 = pywebview | ✅ **Cho phép thư viện nhỏ** (2026-10-03) |
| Q4 | Icon khay hệ thống + popover trạng thái | Có · Không | Có | ⚪ Chưa chốt |
| Q5 | Kiểu nút cửa sổ (nếu frameless) | Đèn giao thông bên trái · Nút Windows bên phải | — | ✅ ~~Kiểu Mac – đèn giao thông bên trái~~ (2026-10-03) → **đổi 2026-10-04: khung cửa sổ gốc của Windows**, nút bên phải. Cửa sổ frameless không có Snap Layouts, kéo vào mép để chia màn hình hay Win+mũi tên, vì Windows không biết đó là thao tác kéo cửa sổ. Thanh tiêu đề tô cùng màu `--window` (DWMWA_CAPTION_COLOR). |
| Q6 | Đường dự phòng (failover) | Không · USB 4G · tethering điện thoại · mạng dây thứ hai | Xác định thiết bị đang có | ⚪ Chưa chốt |
| Q7 | Phạm vi | Chỉ PC này · nhiều PC trong nhà · can thiệp cả router | Chỉ PC này (giai đoạn đầu) | ⚪ Chưa chốt |
| Q8 | Quản lý driver trong tool | Chỉ hiển thị/hướng dẫn · rollback tự động | Chỉ hiển thị | ⚪ Chưa chốt |
| Q9 | Ngôn ngữ giao diện | Tiếng Việt · Tiếng Anh · Song ngữ | Chọn được, mặc định tiếng Anh | ✅ Đã chốt 2026-10-04 — [ADR-0006](adr/0006-i18n.md) |
| Q10 | Watchdog mặc định | Tắt · Bật | Tắt, người dùng tự bật | ⚪ Chưa chốt |
| Q11 | Có kéo được dây LAN tới PC không? | — | — | ✅ **Không** (2026-10-03): PC ở lầu trên, router ở lầu dưới. Người dùng đặt mua ăng-ten Wi‑Fi có dây nối dài (EXP-011). Phương án thay thế: Powerline, repeater/mesh Huawei ở lầu trên + cable vào PC, dây LAN dẹt dọc cầu thang |
| Q12 | Thiết bị mesh nào? | Router Huawei hỗ trợ Mesh+ · hệ mesh hãng khác ở AP mode · thay card Wi‑Fi Intel | Chờ kết quả EXP-005 | ✅ **Huawei WiFi AX3** đặt ở lầu trên, ghép Mesh+ với BE3 Pro, cable AX3 → PC (2026-10-03) — EXP-012 |

## Ghi chú trao đổi

_Thêm các điểm cần bàn ở đây._
