# Đóng góp & quy ước

## Nguyên tắc cốt lõi

1. **Không thay đổi hệ thống mà không thể khôi phục.** Mọi tweak phải lưu giá trị gốc vào `data/backup.json` *trước* khi áp dụng, và có đường khôi phục khi không có bản sao lưu (về mặc định của Windows/driver).
2. **Đọc trước, ghi sau.** Mọi tweak phải có hàm đọc trạng thái độc lập, không có tác dụng phụ.
3. **Minh bạch.** Mọi thay đổi hệ thống và mọi hành động của watchdog đều được ghi vào nhật ký.
4. **Chạy được ở chế độ chỉ-đọc.** Thiếu quyền Admin thì tool vẫn theo dõi và chẩn đoán được, chỉ khóa các thao tác ghi.

## Thêm một tweak mới

1. Thêm mục vào [docs/TWEAKS.md](docs/TWEAKS.md) trước: mục đích, giá trị đích, giá trị mặc định, rủi ro, có ngắt mạng / cần khởi động lại không, nguồn tham khảo.
2. Cài đặt class trong `app/tweaks.py` với đủ 4 thao tác: `read`, `capture`, `apply`, `restore`.
3. Tweak không áp dụng được cho phần cứng hiện tại phải trả về `supported: false` kèm lý do, không được ném lỗi.
4. Phân loại rủi ro:
   - `low` — khuyến nghị rộng rãi, hầu như không có tác dụng phụ.
   - `medium` — có đánh đổi (hiệu năng, pin) hoặc cần khởi động lại.
   - `experimental` — chỉ nên bật để thử, theo dõi số liệu trước/sau.

## Quy ước code

- Python 3.11+, ưu tiên thư viện chuẩn (xem [ADR-0001](docs/adr/0001-python-stdlib-web-ui.md)).
- Gọi PowerShell qua helper chung (`-EncodedCommand`, bắt lỗi thống nhất), không tự ghép chuỗi lệnh.
- Tên biến, hàm, lớp, tham số, khóa cấu hình, khóa dịch và comment trong code **bằng tiếng Anh** — không dùng tiếng Việt, kể cả không dấu. `tests/test_naming.py` kiểm tra tự động.
- Chữ hiển thị cho người dùng (giao diện, chẩn đoán, toast, nhật ký) đi qua catalog dịch `app/locales/<mã>.json`, mặc định tiếng Anh ([ADR-0006](docs/adr/0006-i18n.md)). Tài liệu trong `docs/` vẫn viết tiếng Việt.

## Commit

Theo [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

Cập nhật `CHANGELOG.md` mục `[Unreleased]` trong cùng commit với thay đổi.

## Giấy phép và CLA

Mã nguồn phát hành theo [GPL-3.0](LICENSE). Người đóng góp ký [CLA](CLA.md) một lần qua CLA Assistant ở pull request đầu tiên: bạn vẫn giữ bản quyền, và cho phép Ambysto quản lý giấy phép của dự án về sau. Thư viện bên thứ ba và giấy phép của chúng được liệt kê trong `THIRD-PARTY-NOTICES.txt`, sinh ra khi build.
