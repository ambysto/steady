# ADR-0002: Giao diện phong cách macOS trong cửa sổ pywebview

- **Trạng thái:** Accepted
- **Ngày:** 2026-10-03
- **Sửa đổi:** [ADR-0001](0001-python-stdlib-web-ui.md) (nới lỏng chính sách "chỉ thư viện chuẩn")

## Bối cảnh

Người dùng muốn giao diện mang cảm giác ứng dụng macOS native. Tool vẫn chạy trên Windows và giữ backend Python + PowerShell (xem [UI-DESIGN.md](../UI-DESIGN.md)).

## Quyết định

1. **Hướng A**: ngôn ngữ thiết kế macOS (System Settings / HIG) cho ứng dụng Windows. Không làm app macOS thật.
2. **Vỏ ứng dụng: pywebview** (WebView2), cửa sổ frameless, title bar tự vẽ.
3. **Nút điều khiển cửa sổ kiểu Mac**: 3 nút "đèn giao thông" (đóng / thu nhỏ / phóng to) ở **góc trái** title bar.
4. Cho phép thêm **thư viện nhỏ, có mục đích rõ ràng** (pywebview; ứng viên: pystray, Pillow cho icon khay). Backend lõi vẫn ưu tiên thư viện chuẩn.
5. Font **Inter** (OFL) và icon **Lucide/Phosphor** (MIT) đóng gói cục bộ; không dùng SF Pro / SF Symbols vì giới hạn bản quyền.
6. Giao diện vẫn là HTML/CSS/JS phục vụ từ backend cục bộ ⇒ mở được cả trong trình duyệt khi cần gỡ lỗi.

## Hệ quả

- ✅ Cảm giác ứng dụng thật: không thanh địa chỉ, có cửa sổ riêng, hiệu ứng nền Mica/Acrylic trên Windows 11.
- ⚠️ Cần WebView2 Runtime (có sẵn trên Windows 11) và `pip install pywebview`.
- ⚠️ Nút đèn giao thông bên trái khác thói quen Windows; cần xử lý kéo cửa sổ, double-click phóng to, và Snap Layouts bằng tay.
- 📌 Cần thêm `requirements.txt` khi bắt đầu code.

## Ghi chú khi triển khai (SIC-29, 2026-10-04)

- pywebview 6.2.1 + WebView2. Script pywebview chèn vào trang không bị CSP của server chặn; `window.pywebview.api` có sẵn cho thanh tiêu đề tự vẽ.
- Cửa sổ frameless của pywebview trên Windows (`FormBorderStyle = None`) **không có viền kéo đổi kích thước** và không có Snap Layouts ⇒ thêm góc kéo dưới-phải gọi `window.resize`; phóng to/thu lại qua nút xanh hoặc bấm đúp thanh tiêu đề.
- Chưa bật Mica/Acrylic: nội dung WebView2 không trong suốt nên hiệu ứng không thấy được; giữ nền đặc theo `tokens.css`.
- Windows 11 mặc định đưa icon khay mới vào nhóm ẩn "^"; người dùng kéo ra thanh tác vụ nếu muốn thấy thường trực.
- Monitor (Task Scheduler) và vỏ desktop là hai tiến trình: vỏ chỉ đọc `server.json` + token trong trang, gọi API như trình duyệt.
