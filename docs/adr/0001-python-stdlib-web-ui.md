# ADR-0001: Backend Python và giao diện web chạy cục bộ

- **Trạng thái:** Accepted — sửa đổi bởi [ADR-0002](0002-macos-style-ui-pywebview.md) (cho phép thêm pywebview và thư viện nhỏ)
- **Ngày:** 2026-10-03

## Bối cảnh

Tool cần: đọc/ghi cấu hình mạng Windows (PowerShell, netsh, powercfg, registry), theo dõi ping liên tục, hiển thị biểu đồ realtime, và vẫn dùng được khi mất Internet. Máy phát triển có sẵn Python 3.11 và Node 26; PowerShell chỉ có bản 5.1.

## Quyết định

- Backend **Python 3.11+**, ưu tiên thư viện chuẩn: `http.server`, `sqlite3`, `ctypes` (ICMP, kiểm tra Admin), `winreg`, `subprocess`.
- Giao diện **HTML/CSS/JS thuần**, không tải tài nguyên từ CDN (phải chạy được khi mất mạng).
- Server chỉ bind `127.0.0.1`, bảo vệ bằng token (xem [SECURITY.md](../SECURITY.md)).

## Hệ quả

- ✅ Không cần cài đặt thêm, dễ đọc và sửa.
- ✅ Giao diện có thể tái sử dụng nếu sau này bọc trong cửa sổ desktop.
- ⚠️ Giao diện trong tab trình duyệt không cho cảm giác ứng dụng native — đang xem xét lại tại [OPEN-QUESTIONS.md](../OPEN-QUESTIONS.md) Q2/Q3; quyết định đó có thể sửa đổi ADR này.
