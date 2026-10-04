# Bảo mật

Mô hình tiến trình theo [ADR-0005](adr/0005-unelevated-server-uac-writes.md): **server chạy quyền thường** trong tiến trình monitor; thao tác cần Admin chạy trong tiến trình con riêng, xin quyền qua **UAC mỗi lần**. Dù vậy server vẫn điều khiển được những việc gây gián đoạn (kết nối lại Wi‑Fi, bật watchdog, kích hộp thoại UAC), nên phải chặn mọi nguồn gọi không phải giao diện của chính tool.

## Mối đe dọa & biện pháp

| Mối đe dọa | Biện pháp |
|---|---|
| Máy khác trong LAN gọi API | Chỉ bind `127.0.0.1`, không bind `0.0.0.0` |
| Trang web bất kỳ trong trình duyệt gửi request tới `127.0.0.1` (CSRF) | Token ngẫu nhiên (`secrets`, 256 bit) sinh mỗi lần khởi động, nhúng vào trang giao diện; mọi `/api/*` bắt buộc header `X-Token`, so sánh hằng thời gian. Header tùy biến buộc trình duyệt preflight CORS — server từ chối `OPTIONS` và không trả header CORS nào. Thêm lớp thứ hai cho request ghi: từ chối nếu `Origin` không phải của chính server, hoặc `Sec-Fetch-Site: cross-site` |
| DNS rebinding (trang lạ trỏ tên miền của nó về 127.0.0.1 để đọc được trang chứa token) | `Host` phải đúng `127.0.0.1:<port>` hoặc `localhost:<port>`, áp dụng cho **mọi** đường dẫn, kể cả trang giao diện |
| Nhúng giao diện vào khung của trang lạ (clickjacking) | `Content-Security-Policy: frame-ancestors 'none'`, `X-Frame-Options: DENY` |
| Đọc file ngoài thư mục giao diện | Chỉ phục vụ file trong `web/`, kiểm đường dẫn sau khi chuẩn hóa, chỉ một số đuôi file |
| Làm treo server (body lớn, kết nối chậm) | Giới hạn body 64 KB, timeout socket 10 s, việc lâu (chẩn đoán, đọc tweak) chạy nền và chỉ một việc cùng loại tại một lúc |
| Chèn lệnh vào PowerShell / netsh | Không ghép chuỗi: PowerShell nhận chuỗi qua base64 (`ps_literal`), netsh/ipconfig nhận từng argv riêng; tên interface/profile/tweak được kiểm định dạng |
| Tiến trình quyền thường bị chiếm, dùng tiến trình con Admin làm bàn đạp | Tiến trình con chỉ chạy lệnh trong danh sách cố định, tự kiểm lại tham số, chỉ ghi kết quả vào `%LOCALAPPDATA%\StableInternet\results\<uuid>.json`; mỗi lần đều cần người dùng bấm UAC |
| Thay đổi hệ thống không khôi phục được | Backup giá trị gốc trước khi áp dụng, kiểm chứng, hoàn tác khi lỗi — xem [ADR-0003](adr/0003-tweak-framework.md) |
| Watchdog tự gây mất mạng liên tục | Giới hạn trong [WATCHDOG.md](WATCHDOG.md) / [ADR-0004](adr/0004-watchdog-safety.md) |

## Ngoài phạm vi

- Phần mềm độc hại đã chạy với quyền của người dùng trên máy (có thể đọc token từ bộ nhớ/tiến trình, hoặc tự gọi UAC).
- Người dùng chủ động bấm đồng ý một hộp thoại UAC mà họ không yêu cầu.
