# Architecture Decision Records

Mỗi quyết định kiến trúc là một file `NNNN-tieu-de.md` với các mục: **Trạng thái** (Proposed / Accepted / Superseded), **Bối cảnh**, **Quyết định**, **Hệ quả**.

| ADR | Tiêu đề | Trạng thái |
|---|---|---|
| [0001](0001-python-stdlib-web-ui.md) | Backend Python và giao diện web chạy cục bộ | Accepted (sửa đổi bởi 0002) |
| [0002](0002-macos-style-ui-pywebview.md) | Giao diện phong cách macOS trong cửa sổ pywebview | Accepted |
| [0003](0003-tweak-framework.md) | Khung tweak: sao lưu trước, kiểm chứng sau, rollback khi lỗi | Accepted |
| [0004](0004-watchdog-safety.md) | Watchdog: quyết định thuần, giới hạn bền vững, ngắt mạch tự tắt | Accepted |
| [0005](0005-unelevated-server-uac-writes.md) | Server chạy quyền thường; thao tác cần Admin chạy riêng qua UAC | Accepted |
| [0006](0006-i18n.md) | Đa ngôn ngữ: catalog JSON, mặc định tiếng Anh, lưu thông điệp chứ không lưu chữ | Accepted |
| [0007](0007-measured-impact.md) | Đo hiệu quả một thay đổi bằng số liệu monitor trước/sau | Accepted |
| [0008](0008-failover.md) | Failover sang đường dự phòng bằng metric của interface | Accepted |
