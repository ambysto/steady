# Định hướng giao diện

> **Đã chốt (2026-10-03):** hướng A, cửa sổ pywebview, nút điều khiển kiểu Mac — xem [ADR-0002](adr/0002-macos-style-ui-pywebview.md).

## Câu hỏi: thiết kế theo hướng "macOS native"

Có hai cách hiểu, khác nhau rất lớn về khối lượng công việc:

| | A. Phong cách macOS trên Windows | B. Ứng dụng macOS thật |
|---|---|---|
| Chạy trên | Windows (máy hiện tại) | Mac |
| Công nghệ | Web UI + cửa sổ desktop (pywebview / Tauri) | Swift + SwiftUI, Xcode |
| Backend | Giữ nguyên (Python + PowerShell) | Viết lại hoàn toàn cho macOS |
| Tweak hiện có | Giữ nguyên | Phần lớn không tồn tại trên macOS (driver properties, ASPM, PnPCapabilities…) |
| Phân phối | Thư mục/exe | Cần Apple Developer account để ký & notarize |

**Đề xuất: A** — dùng ngôn ngữ thiết kế macOS cho tool Windows.

## Những gì cần cho hướng A

### 1. Ngôn ngữ thiết kế
Tham chiếu **System Settings** (macOS Sonoma/Sequoia) và Apple Human Interface Guidelines:
- Bố cục **sidebar + nội dung**: sidebar trong mờ (vibrancy) liệt kê Tổng quan · Tối ưu · Chẩn đoán · Nhật ký.
- **Grouped inset list**: nhóm thiết lập trong khối bo góc ~10px, mỗi dòng gồm nhãn, mô tả phụ, control bên phải.
- Control: switch kiểu macOS, segmented control, popover, **sheet** xác nhận cho tweak rủi ro.
- Typography: body 13px, tiêu đề lớn; lưới 8pt; dùng ít màu, màu nhấn (accent) theo hệ thống.
- Sáng/tối tự động theo hệ thống; chuyển động mềm (spring), không gạch chân khi hover.

### 2. Font & icon — lưu ý bản quyền
- **SF Pro** và **SF Symbols** chỉ được cấp phép dùng trên nền tảng Apple ⇒ không đóng gói vào tool Windows.
- Thay thế: font **Inter** (OFL) gần giống SF; hoặc `system-ui` (Windows sẽ ra Segoe UI Variable).
- Icon: **Lucide** / **Phosphor** (MIT) — nét mảnh tương tự SF Symbols.

### 3. Cửa sổ ứng dụng (thay vì tab trình duyệt)
Một tab trình duyệt không thể cho cảm giác native. Lựa chọn:

| Lựa chọn | Ưu | Nhược |
|---|---|---|
| **pywebview** (WebView2) | Giữ Python, nhẹ, cửa sổ frameless | Thêm dependency (phá ADR-0001 "chỉ thư viện chuẩn") |
| Tauri | Rất nhẹ, đóng gói tốt | Thêm Rust toolchain, backend tách process |
| Electron | Hệ sinh thái lớn | Nặng (~150MB) |
| Tab trình duyệt | Không cần gì thêm | Không có cảm giác ứng dụng |

Với cửa sổ frameless cần tự vẽ title bar. Nút điều khiển cửa sổ: nút kiểu "đèn giao thông" bên trái (giống Mac, lạ với người dùng Windows) hay nút Windows bên phải → **đã chốt (2026-10-04): dùng khung gốc của Windows**, không frameless. Thanh tiêu đề tự vẽ không có Snap Layouts, không chia màn hình khi kéo vào mép và không có Win+mũi tên; khung gốc có sẵn hết. Thanh tiêu đề được tô cùng màu nền trang.

Hiệu ứng nền mờ thật: Windows 11 có **Mica/Acrylic** qua `DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)` — gọi được bằng ctypes trên handle cửa sổ; kết hợp CSS `backdrop-filter` cho sidebar.

### 4. Biểu tượng khay hệ thống (tương đương menu bar extra của macOS)
Rất hợp với tool theo dõi mạng: icon xanh/vàng/đỏ theo trạng thái, bấm vào mở popover nhỏ (ping hiện tại, mất gói, nút "Kết nối lại"). Cần `pystray` + `Pillow`, hoặc tự gọi `Shell_NotifyIcon` qua ctypes.

### 5. Chi tiết tạo cảm giác native
- Phím tắt (`Ctrl+,` mở cài đặt, `Ctrl+R` chạy chẩn đoán).
- Thông báo hệ thống (Windows toast) thay cho popup trong trang.
- Không có cuộn ngang, không hiện thanh địa chỉ, không menu chuột phải của trình duyệt.
- Phản hồi tức thì: toggle chuyển trạng thái "đang áp dụng…" rồi xác nhận.

### 6. Quy trình thiết kế
1. Mockup tĩnh (HTML) cho 4 màn hình + popover khay → duyệt.
2. Bộ design token (màu, khoảng cách, bo góc, font) dùng chung.
3. Dựng giao diện thật trên token đó.

## Mockup và design token (SIC-27, đã duyệt 2026-10-04)

- **Tên hiển thị:** "Ambysto Steady" (khóa `app.name`, không dịch; đổi 2026-10-04 từ "Stable Internet Connection", nay là câu mô tả). Ambysto là tên công ty, gốc từ tên khoa học của loài kỳ giông nước (axolotl). "StableInternet" chỉ còn là tên repo/thư mục/task/mutex nội bộ.
- **Khổ hẹp (< 760px):** thanh bên chuyển thành thanh tab ngang phía trên — điều hướng không bao giờ bị ẩn.

- **Mở xem:** `docs/mockup/dist/stableinternet-mockup.html` (một file, mở thẳng bằng trình duyệt). Bản nguồn: `docs/mockup/mockup.html`, `mockup.css`, `mockup.js`; chạy `python scripts/build_mockup.py --bundle` sau khi sửa nguồn hoặc catalog.
- **Màn hình:** Tổng quan (trạng thái, biểu đồ độ trễ router/Internet, số liệu, sự cố gần đây, thao tác nhanh, watchdog) · Tối ưu (nhóm theo `tweak.group.*`, nhãn rủi ro/Admin/mất mạng, switch, sheet xác nhận cho tweak thử nghiệm) · Chẩn đoán (13 kiểm tra mở rộng được, "Nên làm gì", bufferbloat theo yêu cầu) · Nhật ký (lọc Tất cả/Sự cố/Watchdog/Thay đổi, nhóm theo ngày) · Cài đặt (**Ngôn ngữ**, giao diện Sáng/Tối/Theo Windows, chạy cùng Windows, thông báo, watchdog) · popover khay. Thanh trên cùng (chỉ có trong mockup) giả lập mất mạng và bật/tắt popover.
- **Chữ:** toàn bộ lấy từ `app/locales/*.json` (khóa `ui.*` và chính các thông điệp `diag.*`/`event.*`/`watchdog.*` của backend), đổi ngôn ngữ trong Cài đặt có hiệu lực ngay. Dữ liệu mẫu lấy từ máy phát triển ngày 2026-10-04.
- **Design token:** `web/tokens.css` — font (Inter → Segoe UI Variable), cỡ chữ 11/12/13/15/22/28, lưới 4 pt, bo góc 10 (nhóm/cửa sổ) và 6 (control), bảng màu sáng/tối theo ngữ nghĩa (`--ok/--warn/--bad/--info`, `--surface`, `--separator`…), chuyển động ngắn và tắt khi `prefers-reduced-motion`. Dark mode theo hệ thống, ép bằng `data-theme`. Giao diện thật (SIC-28) dùng lại file này.
- **Chưa có trong mockup:** font Inter đóng gói (đang dùng font hệ thống), hiệu ứng Mica thật của cửa sổ, biểu tượng khay.
