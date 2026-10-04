# CLAUDE.md

Hướng dẫn cho AI agent làm việc trong repo này.

## Dự án

**Ambysto Steady** (tên hiển thị, khóa `app.name`; công ty Ambysto) — tool Windows theo dõi, tự khôi phục và tối ưu kết nối mạng. `StableInternet` chỉ còn là tên nội bộ (repo, package, thư mục dữ liệu, mutex). Backend Python, giao diện web tại `127.0.0.1`. Đọc [README.md](README.md) và [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) trước khi sửa code.

## Quy tắc bắt buộc

- **Không tự áp dụng tweak lên máy thật** trong lúc phát triển/kiểm thử. Chỉ chạy các thao tác đọc; thao tác ghi (Set-NetAdapterAdvancedProperty, powercfg /set…, ghi registry, Restart-NetAdapter) chỉ chạy khi người dùng đồng ý rõ ràng.
- Mọi tweak mới phải được mô tả trong [docs/TWEAKS.md](docs/TWEAKS.md) trước khi viết code.
- Các quyết định kiến trúc mới → thêm ADR trong `docs/adr/`.
- Cập nhật `CHANGELOG.md` khi thay đổi hành vi.
- **Tên trong code luôn bằng tiếng Anh**: hàm, biến, lớp, tham số, khóa cấu hình, khóa dịch, biến PowerShell. Không dùng tiếng Việt, kể cả tiếng Việt không dấu (`kiem_tra_mang`, `ketNoi` đều sai). `tests/test_naming.py` chặn tự động.
- Chữ người dùng thấy không viết cứng trong code: đi qua `app/locales/*.json` (mặc định tiếng Anh) — xem [ADR-0006](docs/adr/0006-i18n.md).

## Môi trường máy phát triển

- Windows 11 Pro, PowerShell 5.1 (không có `pwsh`), Python 3.11, Node 26.
- PowerShell 5.1: không có `&&`, `Test-Connection -TargetName`; dùng `System.Net.NetworkInformation.Ping` hoặc ICMP qua ctypes.
- Output `netsh` là tiếng Anh, mã hóa OEM.

## Lệnh hữu ích (chỉ đọc)

```powershell
netsh wlan show interfaces
Get-NetAdapterAdvancedProperty -Name 'Wi-Fi'
powercfg /q SCHEME_CURRENT 501a4d13-42af-4429-9fd1-a8218c268e20 ee12f906-d277-404b-b6da-e5fa1a576df5
pnputil /enum-drivers /class Net
```
