# ADR-0005: Server chạy quyền thường; thao tác cần Admin chạy riêng qua UAC

- **Trạng thái:** Accepted — thay giả định "backend chạy quyền Administrator" trong SECURITY.md bản đầu
- **Ngày:** 2026-10-04

## Bối cảnh

Bản thiết kế đầu cho cả backend chạy quyền Admin. Thực tế:
- Monitor (nơi có dữ liệu sống) chạy 24/7 qua Task Scheduler với **quyền thường** ([ADR-0004](0004-watchdog-safety.md), SIC-11).
- Server HTTP là phần tiếp xúc nhiều nhất với dữ liệu không tin cậy (mọi trang web trong trình duyệt đều gửi được request tới `127.0.0.1`).
- Thao tác cần Admin hiếm: bật/tắt tweak, khởi động lại card.

## Quyết định

1. **Server chạy trong tiến trình monitor, quyền thường.** Đọc dữ liệu sống trực tiếp từ bộ nhớ monitor; không bao giờ tự nâng quyền.
2. **Thao tác cần Admin** chạy trong một tiến trình con riêng (`python -m app.elevated`) được khởi chạy bằng `ShellExecuteEx` động từ `runas` ⇒ Windows hiện **hộp thoại UAC cho mỗi lần**. Tiến trình con chỉ nhận một **danh sách lệnh cố định** (bật/tắt một tweak có trong danh mục, khởi động lại card theo tên hợp lệ), tự kiểm tra lại tham số (không tin tiến trình cha), và chỉ ghi kết quả vào thư mục riêng của người dùng.
3. Nếu chính tiến trình đã có quyền Admin (người dùng cài task với `--highest`) thì thực hiện trực tiếp, không qua UAC — mọi lớp bảo vệ của server vẫn giữ nguyên.
4. **Watchdog không xin UAC** (không ai ngồi trước máy để bấm): thiếu quyền thì bỏ qua bước khởi động lại card như đã có.
5. Bảo vệ server (chi tiết trong [SECURITY.md](../SECURITY.md)): chỉ bind `127.0.0.1`; kiểm `Host` (chống DNS rebinding); token ngẫu nhiên mỗi lần khởi động, nhúng vào trang giao diện, bắt buộc header `X-Token` cho mọi `/api/*` (so sánh hằng thời gian); request ghi bị từ chối nếu `Origin` lạ hoặc `Sec-Fetch-Site: cross-site`; không trả header CORS nào; header bảo mật (CSP, chống nhúng khung, `nosniff`); giới hạn kích thước body và thời gian chờ socket.

## Hệ quả

- ✅ Lỗi ở server không thành lỗi chiếm quyền Admin; kẻ tấn công vượt qua được server cũng chỉ kích được hộp thoại UAC mà người dùng phải tự bấm.
- ✅ Mỗi thay đổi hệ thống có sự đồng ý rõ ràng từ Windows, đúng tinh thần "chỉ ghi khi người dùng đồng ý".
- ⚠️ Mỗi lần bật/tắt tweak có một hộp thoại UAC. Chấp nhận được vì thao tác này hiếm.
- ⚠️ Hộp thoại UAC xuất phát từ tiến trình nền có thể chỉ nhấp nháy trên thanh tác vụ thay vì hiện ngay lên trước.
